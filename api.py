"""
AIRI API — one focused endpoint on top of the core library, plus the
try-it-out page as a static file. Run it with:

    uvicorn api:app --reload

Then open http://127.0.0.1:8000/
"""

import io
import os
import secrets
import time
import zipfile
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from airi import Archetype, analyze, build_report, list_supported_models, project
from airi import author, db, runtime_config, workspaces as ws
from airi.analyzer import build_result_from_counts
from airi.auth import (
    CODE_TTL_SECONDS,
    MAX_VERIFY_ATTEMPTS,
    AuthError,
    create_admin_token,
    create_session_token,
    extract_bearer_token,
    generate_code,
    hash_code,
    normalize_email,
    verify_admin_token,
    verify_code,
    verify_session_token,
)
from airi.email_provider import EmailSendError, send_member_added_email, send_member_removed_email, send_otp_email
from airi.exact_provider import ExactCountUnavailable, count_tokens_exact, has_exact_provider
from airi.projector import MAX_ARCHETYPES
from airi.registry import MODEL_REGISTRY, get_model_spec, is_known_model
from airi.report import MAX_RECORDS
from airi.report_render import render_report_html

# --- "Exact" flavor config (all optional — absence just means Exact mode
# returns a clear 503 instead of a raw error; the default flavor above
# never touches any of this) ---
OTP_REQUEST_COOLDOWN_SECONDS = 60   # per email, between code requests
OTP_DAILY_REQUEST_LIMIT = 5         # per email, per rolling 24h — protects the shared Resend daily quota
EXACT_CALLS_PER_MINUTE = 20         # per signed-in user — protects the shared Anthropic/Google API keys


def _get_auth_secret() -> str:
    secret = os.environ.get("AUTH_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Sign-in isn't configured on this deployment yet.")
    return secret


def _get_provider_api_key(provider: str) -> Optional[str]:
    """AIRI's own shared, server-held key for a provider — used only in
    "test mode" (see runtime_config.py). These are meant as demo/testing
    keys, not a production key pool: once test mode is off, exact-mode
    calls use each signed-in user's own BYOK key instead (see
    ExactAnalyzeRequest / analyze_exact below)."""
    return {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "google": os.environ.get("GOOGLE_API_KEY"),
    }.get(provider)


def _get_admin_password() -> str:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        raise HTTPException(status_code=503, detail="The admin page isn't configured on this deployment yet.")
    return password


def _require_session_email(authorization: Optional[str]) -> str:
    """Shared by every workspaces/projects/profile endpoint: 401 on a
    missing/invalid/expired session token, else the signed-in email."""
    secret = _get_auth_secret()
    try:
        return verify_session_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


def _require_user_id(authorization: Optional[str]) -> int:
    """Resolves the session's email to a `users.id`. A signed-in session
    with no matching user row shouldn't happen (verify-code always
    upserts one first) — treated as an expired/invalid session rather
    than a 500 if it somehow does."""
    email = _require_session_email(authorization)
    try:
        user_id = db.get_user_id_by_email(email)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Your session is no longer valid — sign in again.")
    return user_id


def _get_owned_workspace(workspace_id: int, user_id: int) -> dict:
    """404 (not 403) whether the workspace doesn't exist or just isn't
    this user's — never confirms another workspace id exists."""
    try:
        workspace = db.get_workspace(workspace_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if workspace is None or workspace["owner_user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace


def _get_owned_project(project_id: int, user_id: int) -> Tuple[dict, dict]:
    """Returns (project, workspace) after confirming the project's parent
    workspace belongs to this user. Same 404-not-403 reasoning."""
    try:
        proj = db.get_project(project_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    workspace = _get_owned_workspace(proj["workspace_id"], user_id)
    return proj, workspace


def _require_app_key_confirmed(user_id: int, submitted_app_key: Optional[str]) -> None:
    """Gate for every destructive workspace/project action (requirement:
    re-enter the profile app key before any delete). 400 either way —
    this is a per-caller, fixable problem, not a deployment one:
      - no app key saved yet: tell them to set one up first (there's
        nothing to confirm against otherwise, so deletion is blocked
        rather than silently allowed).
      - key missing/blank/wrong: a plain "incorrect" message, matching
        the OTP-verify convention of never hinting at which part failed.
    """
    try:
        profile = db.get_user_profile(user_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not profile or not profile.get("app_key_hash"):
        raise HTTPException(
            status_code=400,
            detail="Set an app key in your profile before deleting anything — it's used to confirm destructive actions.",
        )
    secret = _get_auth_secret()
    if not ws.verify_app_key(user_id, submitted_app_key or "", secret, profile["app_key_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect app key.")


def _require_admin(authorization: Optional[str]) -> None:
    """Raises 503 (not configured) or 401 (bad/missing/expired token) —
    used to gate every /admin/* endpoint except /admin/login itself."""
    secret = _get_auth_secret()
    try:
        verify_admin_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))


# In-process sliding-window limiter for /analyze/exact, keyed by the
# signed-in user's email. Intentionally not DB-backed: it resets on a
# redeploy/restart, which is fine for an abuse guard (not a security
# boundary) and keeps every exact-mode request to zero extra DB round
# trips — the DB is only touched at login time (see airi/db.py).
_exact_call_log: Dict[str, deque] = defaultdict(deque)


def _check_exact_rate_limit(email: str) -> None:
    now = time.monotonic()
    window_start = now - 60
    log = _exact_call_log[email]
    while log and log[0] < window_start:
        log.popleft()
    if len(log) >= EXACT_CALLS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Exact mode is rate-limited — please slow down and try again shortly.")
    log.append(now)

app = FastAPI(
    title="AIRI — AI Request Intelligence",
    description="Estimate tokens, context usage and cost for an AI request before you send it.",
    version="0.2.0",
)

# Wide open for the MVP: this is a stateless, read-only analysis endpoint
# with no auth and no user data, meant to be called from any frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

MAX_PROMPT_CHARS = 200_000  # guardrail so a runaway request can't hang the process


class ChatMessage(BaseModel):
    role: str
    content: str


def _check_input_shape(prompt: Optional[str], messages: Optional[List[ChatMessage]], label: str = "Request"):
    """Shared validation for anything shaped like a single analyze() call —
    used by both /analyze and each archetype inside /project."""
    if not prompt and not messages:
        raise ValueError(f"{label}: provide either `prompt` or `messages`.")
    if prompt and messages:
        raise ValueError(f"{label}: provide only one of `prompt` or `messages`, not both.")
    text_len = len(prompt) if prompt else sum(len(m.content) for m in messages)
    if text_len > MAX_PROMPT_CHARS:
        raise ValueError(f"{label}: input exceeds the {MAX_PROMPT_CHARS}-character request limit.")


class AnalyzeRequest(BaseModel):
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="gpt-4o")
    expected_output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate(self):
        _check_input_shape(self.prompt, self.messages)
        return self


class RequestCodeBody(BaseModel):
    email: str


class VerifyCodeBody(BaseModel):
    email: str
    code: str


MAX_BYOK_KEY_CHARS = 512  # generous but bounded — every real provider key is well under this


class ExactAnalyzeRequest(AnalyzeRequest):
    """Same shape as AnalyzeRequest, plus two optional BYOK fields used
    only when this deployment is out of "test mode" (see runtime_config.py
    and docs/ADMIN.md). Neither field is ever written to the database,
    logged, or echoed back — each is used, at most, for the single
    provider call this one request makes, then discarded with the rest
    of the request body."""

    anthropic_api_key: Optional[str] = Field(default=None, max_length=MAX_BYOK_KEY_CHARS)
    google_api_key: Optional[str] = Field(default=None, max_length=MAX_BYOK_KEY_CHARS)


class AdminLoginBody(BaseModel):
    password: str


class AdminConfigBody(BaseModel):
    test_mode: bool


class AuthorProfileBody(BaseModel):
    """All optional/defaulted — a field left out is stored as "" (this
    is a full replace, same semantics as AdminConfigBody's test_mode;
    the admin page always sends its whole form, so nothing gets
    silently wiped). Real validation (length caps, photo shape) happens
    in airi/author.py, not here — Pydantic just gets the types right."""

    name: str = ""
    title: str = ""
    company: str = ""
    tagline: str = ""
    bio: str = ""
    location: str = ""
    email: str = ""
    website: str = ""
    linkedin: str = ""
    twitter: str = ""
    github: str = ""
    photo_data_url: str = ""


class AppKeyBody(BaseModel):
    app_key: str = Field(min_length=1, max_length=4)


class AppKeyConfirmBody(BaseModel):
    """Sent on every destructive workspaces/projects call — see
    _require_app_key_confirmed. Optional/defaulted so a caller who
    hasn't set up an app key yet still gets the clear "set one up
    first" 400 rather than a 422 for a missing field."""

    app_key: str = ""


class WorkspaceBody(BaseModel):
    title: str = ""
    target: str = ""
    description: str = ""


class WorkspaceMemberBody(BaseModel):
    email: str = ""


class ProjectBody(BaseModel):
    title: str = ""
    description: str = ""
    tech_stack: Dict[str, str] = Field(default_factory=dict)


class ArchetypeRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    volume: int = Field(ge=0)
    prompt: Optional[str] = None
    messages: Optional[List[ChatMessage]] = None
    model: str = Field(default="gpt-4o")
    expected_output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate(self):
        _check_input_shape(self.prompt, self.messages, label=f"Archetype '{self.name}'")
        return self


class ProjectRequest(BaseModel):
    archetypes: List[ArchetypeRequest] = Field(min_length=1, max_length=MAX_ARCHETYPES)


class ReportRequest(BaseModel):
    """
    A load-test report is built from records your own test harness
    already has — one per AI request, in the exact shape `/analyze`
    returns, plus a `label` (required) and optional `phase`/`timestamp`.
    Collect these as you run your suite (in any language), then submit
    everything you collected in one call at the end of the run. See
    docs/INTEGRATION.md#load-test-token-usage-reporting.
    """

    run_name: str = Field(min_length=1, max_length=200)
    records: List[Dict[str, Any]] = Field(min_length=1, max_length=MAX_RECORDS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/config")
def public_config():
    """Public, unauthenticated: just enough for the frontend to decide
    what to show a signed-in user in the Exact flow — a BYOK key panel
    (test_mode: false) or a "you're using AIRI's testing keys" note
    (test_mode: true). Never exposes whether any secret is actually
    configured — that's only in GET /admin/config, behind the admin
    password."""
    test_mode, _source = runtime_config.get_test_mode()
    return {"test_mode": test_mode}


@app.get("/models")
def models():
    """Model list + metadata for the frontend's dropdown."""
    return [
        {
            "id": model_id,
            "context_window": spec.context_window,
            "input_price_per_1m": spec.input_price_per_1m,
            "output_price_per_1m": spec.output_price_per_1m,
        }
        for model_id, spec in sorted(MODEL_REGISTRY.items())
    ]


@app.post("/analyze")
def analyze_request(body: AnalyzeRequest):
    try:
        result = analyze(
            prompt=body.prompt,
            messages=[m.model_dump() for m in body.messages] if body.messages else None,
            model=body.model,
            expected_output_tokens=body.expected_output_tokens,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.to_dict()


@app.post("/auth/request-code")
def request_code(body: RequestCodeBody):
    """
    Step 1 of email+OTP sign-in for the "Exact" flavor. Always returns the
    same generic message whether or not the email is new, to avoid leaking
    which addresses have used AIRI before. Rate-limited per email (a 60s
    cooldown plus a daily cap) specifically to protect the shared Resend
    sending quota this deployment uses — see docs/EXACT_MODE.md.
    """
    pepper = _get_auth_secret()
    try:
        email = normalize_email(body.email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    now = datetime.now(timezone.utc)
    try:
        if db.count_recent_otp_requests(email, now - timedelta(seconds=OTP_REQUEST_COOLDOWN_SECONDS)) > 0:
            raise HTTPException(status_code=429, detail="Please wait a minute before requesting another code.")
        if db.count_recent_otp_requests(email, now - timedelta(hours=24)) >= OTP_DAILY_REQUEST_LIMIT:
            raise HTTPException(status_code=429, detail="Too many code requests for this email today — try again tomorrow.")

        code = generate_code()
        db.create_otp_code(email, hash_code(email, code, pepper), now + timedelta(seconds=CODE_TTL_SECONDS))
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    try:
        send_otp_email(email, code)
    except EmailSendError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {"message": "Check your email for a 6-digit code. It expires in 10 minutes."}


@app.post("/auth/verify-code")
def verify_code_endpoint(body: VerifyCodeBody):
    """Step 2: exchange the emailed code for a session token (a JWT — no
    session table, so a valid session costs zero DB round trips after this
    point). Store the returned token and send it back as
    `Authorization: Bearer <token>` on /analyze/exact and /auth/me."""
    secret = _get_auth_secret()
    try:
        email = normalize_email(body.email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        row = db.get_latest_unconsumed_code(email)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    if row is None:
        raise HTTPException(status_code=400, detail="No code requested for this email — request a new one.")
    if row["attempts"] >= MAX_VERIFY_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Too many incorrect attempts — request a new code.")
    if datetime.now(timezone.utc) > row["expires_at"]:
        raise HTTPException(status_code=400, detail="That code expired — request a new one.")

    if not verify_code(email, body.code, secret, row["code_hash"]):
        db.increment_attempts(row["id"])
        raise HTTPException(status_code=400, detail="Incorrect code.")

    db.consume_code(row["id"])
    db.upsert_user_login(email)
    return {"token": create_session_token(email, secret), "email": email}


@app.get("/auth/me")
def auth_me(authorization: Optional[str] = Header(default=None)):
    """Lets the frontend check whether a stored token is still valid on
    page load, without re-doing the OTP flow."""
    secret = _get_auth_secret()
    try:
        email = verify_session_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return {"email": email}


# --- Workspaces / Projects (Phase 1 — see docs/WORKSPACES.md) ---
#
# Everything below requires a signed-in session (Authorization: Bearer
# <token> from /auth/verify-code) — this is entirely gated behind the
# same "Exact" flavor sign-in used by /analyze/exact, per the original
# request ("workspaces feature: works only after user logs in with
# OTP"). None of it touches /analyze, /project, /report* — those stay
# completely anonymous and stateless as documented elsewhere.


@app.get("/profile")
def get_profile(authorization: Optional[str] = Header(default=None)):
    """Whether this signed-in user has an app key saved yet — never the
    key or its hash. The frontend uses this to decide whether to show
    "set your app key" or "change your app key" on the profile panel,
    and to gate delete actions behind "you need to set one up first"."""
    user_id = _require_user_id(authorization)
    try:
        profile = db.get_user_profile(user_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"has_app_key": bool(profile and profile.get("app_key_hash"))}


@app.post("/profile/app-key")
def set_app_key(body: AppKeyBody, authorization: Optional[str] = Header(default=None)):
    """Sets or changes the signed-in user's app key. Only ever stores a
    hash (see airi.workspaces.hash_app_key) — the digits themselves are
    never written to the database, logged, or returned by any endpoint."""
    user_id = _require_user_id(authorization)
    secret = _get_auth_secret()
    try:
        app_key = ws.normalize_app_key(body.app_key)
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        db.set_app_key_hash(user_id, ws.hash_app_key(user_id, app_key, secret))
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"has_app_key": True}


def _workspace_detail(workspace: dict) -> dict:
    """A workspace plus its members and projects — everything requirement
    #10 ("when he logs in all the above details must be shown back")
    needs for one workspace in one call."""
    workspace_id = workspace["id"]
    return {
        **workspace,
        "members": db.list_workspace_members(workspace_id),
        "projects": db.list_projects(workspace_id),
    }


@app.get("/workspaces")
def list_workspaces(authorization: Optional[str] = Header(default=None)):
    """Every workspace this signed-in user owns, most recent first, with
    member/project counts — the workspace-list view."""
    user_id = _require_user_id(authorization)
    try:
        return db.list_workspaces(user_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/workspaces")
def create_workspace(body: WorkspaceBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    try:
        title, target, description = ws.validate_workspace_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        workspace = db.create_workspace(user_id, title, target, description)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _workspace_detail(workspace)


@app.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    workspace = _get_owned_workspace(workspace_id, user_id)
    return _workspace_detail(workspace)


@app.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: int, body: WorkspaceBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_owned_workspace(workspace_id, user_id)
    try:
        title, target, description = ws.validate_workspace_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workspace = db.update_workspace(workspace_id, title, target, description)
    return _workspace_detail(workspace)


@app.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    """Deleting a workspace cascades to its members and projects (see
    sql/003_workspaces_schema.sql's ON DELETE CASCADE) — the app-key
    confirmation exists specifically because this one action can take
    an entire workspace's projects with it."""
    user_id = _require_user_id(authorization)
    _get_owned_workspace(workspace_id, user_id)
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_workspace(workspace_id)
    return {"deleted": True}


@app.post("/workspaces/{workspace_id}/members")
def add_workspace_member(workspace_id: int, body: WorkspaceMemberBody, authorization: Optional[str] = Header(default=None)):
    """Adds a team member by email and sends them a standard notification
    (best-effort — see the try/except below: a Resend hiccup shouldn't
    block adding the member, since the membership itself is what matters
    and the notification is a courtesy, not a confirmation the member
    must act on)."""
    user_id = _require_user_id(authorization)
    workspace = _get_owned_workspace(workspace_id, user_id)
    try:
        email = ws.normalize_member_email(body.email)
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    member = db.add_workspace_member(workspace_id, email)
    if member is None:
        raise HTTPException(status_code=409, detail=f"{email} is already a member of this workspace.")

    try:
        send_member_added_email(email, workspace["title"])
    except EmailSendError:
        pass  # membership is saved either way; the email is a courtesy

    return member


@app.delete("/workspaces/{workspace_id}/members/{member_id}")
def remove_workspace_member(workspace_id: int, member_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    workspace = _get_owned_workspace(workspace_id, user_id)
    member = db.remove_workspace_member(workspace_id, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found.")

    try:
        send_member_removed_email(member["email"], workspace["title"])
    except EmailSendError:
        pass

    return {"deleted": True}


@app.get("/projects/tech-stack-categories")
def tech_stack_categories():
    """The tech-stack form's category list (label + required/optional),
    so the frontend never has to hardcode it separately from
    airi/workspaces.py — add a category there and it shows up here."""
    return ws.TECH_STACK_CATEGORIES


@app.post("/workspaces/{workspace_id}/projects")
def create_project(workspace_id: int, body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_owned_workspace(workspace_id, user_id)
    try:
        title, description, tech_stack = ws.validate_project_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return db.create_project(workspace_id, title, description, tech_stack)


@app.get("/projects/{project_id}")
def get_project(project_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    project_row, _workspace = _get_owned_project(project_id, user_id)
    return project_row


@app.put("/projects/{project_id}")
def update_project(project_id: int, body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_owned_project(project_id, user_id)
    try:
        title, description, tech_stack = ws.validate_project_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return db.update_project(project_id, title, description, tech_stack)


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_owned_project(project_id, user_id)
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_project(project_id)
    return {"deleted": True}


def _resolve_exact_api_key(body: "ExactAnalyzeRequest", provider: str, test_mode: bool) -> str:
    """Picks which provider key an /analyze/exact call should use.

    Test mode: AIRI's own shared, server-held key (today's behavior) —
    503 if this deployment hasn't set one for that provider.

    BYOK mode: the caller's own key from the request body — 400 (not
    503; this is a per-caller, fixable problem, not a deployment one) if
    they didn't send one for that provider. The key is used for exactly
    this one provider call and is never stored, logged, or returned."""
    if test_mode:
        api_key = _get_provider_api_key(provider)
        if not api_key:
            raise HTTPException(status_code=503, detail=f"Exact mode for {provider} models isn't configured on this deployment yet.")
        return api_key

    byok_key = {"anthropic": body.anthropic_api_key, "google": body.google_api_key}.get(provider)
    if not byok_key:
        provider_label = {"anthropic": "an Anthropic", "google": "a Google (Gemini)"}.get(provider, "a")
        raise HTTPException(
            status_code=400,
            detail=f"This model needs {provider_label} API key for Exact mode — add one under \"Manage keys\".",
        )
    return byok_key


@app.post("/admin/login")
def admin_login(body: AdminLoginBody):
    """Password-only sign-in for the admin page (frontend/admin.html).
    The password itself lives only in the ADMIN_PASSWORD env var — never
    the database — and is compared in constant time so response timing
    can't leak how much of a guess was correct. Returns a short-lived
    (12h) admin token, distinct from and never interchangeable with a
    user's Exact-mode session token (see airi/auth.py)."""
    secret = _get_auth_secret()
    expected = _get_admin_password()
    if not secrets.compare_digest(body.password, expected):
        raise HTTPException(status_code=401, detail="Incorrect password.")
    return {"token": create_admin_token(secret)}


@app.get("/admin/config")
def admin_get_config(authorization: Optional[str] = Header(default=None)):
    """Admin-only: current test-mode value + where it's coming from, plus
    a read-only checklist of which secrets are configured on this
    deployment. Never returns the secrets themselves — just whether each
    one is set, so an admin can tell "BYOK mode is on but I never set an
    Anthropic key server-side" apart from an actual bug."""
    _require_admin(authorization)
    test_mode, source = runtime_config.get_test_mode()
    return {
        "test_mode": test_mode,
        "source": source,
        "configured": {
            "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "google_key": bool(os.environ.get("GOOGLE_API_KEY")),
            "resend": bool(os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM_EMAIL")),
            "database": bool(os.environ.get("DATABASE_URL")),
        },
    }


@app.post("/admin/config")
def admin_set_config(body: AdminConfigBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: flips test mode on/off. Writes an override to the
    app_config table (see sql/002_app_config.sql) that takes precedence
    over AIRI_TEST_MODE until changed again — see runtime_config.py."""
    _require_admin(authorization)
    try:
        runtime_config.set_test_mode(body.test_mode)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    test_mode, source = runtime_config.get_test_mode()
    return {"test_mode": test_mode, "source": source}


@app.get("/author")
def get_author():
    """Public, unauthenticated: the founder/author profile shown on
    frontend/author.html. Returns all-empty defaults if nothing's been
    set yet (or the database isn't configured) — never a 404 or 503,
    since the page needs to render a friendly "coming soon" state
    either way rather than treat this as an error."""
    return author.get_author_profile()


@app.post("/admin/author")
def admin_set_author(body: AuthorProfileBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: sets the author profile (frontend/admin.html's
    "Author profile" panel). A full replace — see AuthorProfileBody."""
    _require_admin(authorization)
    try:
        saved = author.set_author_profile(body.model_dump())
    except author.AuthorProfileError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return saved


@app.post("/analyze/exact")
def analyze_exact(body: ExactAnalyzeRequest, authorization: Optional[str] = Header(default=None)):
    """
    Same request/response shape as /analyze, but for Anthropic/Google
    models it calls that provider's own (free, no-charge) token-counting
    API instead of the chars/4 heuristic — see airi/exact_provider.py.
    OpenAI models are already exact via tiktoken locally, so this just
    delegates to the same path /analyze uses for those.

    Requires a signed-in session (`Authorization: Bearer <token>` from
    /auth/verify-code). While this deployment is in "test mode" (see
    GET /config and docs/ADMIN.md), Exact calls out using AIRI's own
    shared provider API keys, so sign-in exists to keep that shared
    quota from being hammered by anonymous traffic. Once test mode is
    off, each signed-in user supplies their own key (BYOK) in the
    request body — AIRI never stores it.
    """
    secret = _get_auth_secret()
    try:
        email = verify_session_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    _check_exact_rate_limit(email)

    test_mode, _source = runtime_config.get_test_mode()

    messages = [m.model_dump() for m in body.messages] if body.messages else None
    spec = get_model_spec(body.model)

    if spec.tokenizer_family == "openai" or not has_exact_provider(spec.provider):
        # tiktoken already gives an exact count locally for OpenAI models —
        # no provider call (and so no key, shared or BYOK) needed. For a
        # model with no exact_provider integration (unknown/unsupported),
        # this is the same heuristic /analyze would give; Exact mode has
        # nothing more to offer it.
        result = analyze(prompt=body.prompt, messages=messages, model=body.model, expected_output_tokens=body.expected_output_tokens)
        return result.to_dict()

    api_key = _resolve_exact_api_key(body, spec.provider, test_mode)

    try:
        input_tokens = count_tokens_exact(
            prompt=body.prompt, messages=messages, model=body.model, provider=spec.provider, api_key=api_key,
        )
    except ExactCountUnavailable as exc:
        # Graceful degrade to the heuristic, same spirit as tiktoken's own
        # network fallback — but say so honestly rather than silently
        # returning a heuristic number under an "Exact" label. In BYOK
        # mode the detail is deliberately generic: the caller's own key
        # was just used in a failed provider call, and we never want any
        # chance of that provider's raw response text (which could echo
        # back part of the request) reaching another user's screen.
        result = analyze(prompt=body.prompt, messages=messages, model=body.model, expected_output_tokens=body.expected_output_tokens)
        data = result.to_dict()
        if test_mode:
            data["exact_mode_note"] = f"Exact provider count unavailable right now ({exc}) — showing the heuristic estimate instead."
        else:
            data["exact_mode_note"] = "Exact provider count unavailable right now (check that your API key is valid) — showing the heuristic estimate instead."
        return data

    result = build_result_from_counts(body.model, input_tokens, "provider-api", "high", body.expected_output_tokens)
    return result.to_dict()


@app.post("/project")
def project_request(body: ProjectRequest):
    """
    Volume projection: given several distinct AI call-sites in your app,
    each with a representative sample request, a target model, and a
    volume you supply (from your own analytics or projections), returns
    per-archetype and grand-total tokens/cost. See airi/projector.py —
    AIRI doesn't guess volume, it only does the multiplication once you
    provide it.
    """
    try:
        archetypes = [
            Archetype(
                name=a.name,
                volume=a.volume,
                prompt=a.prompt,
                messages=[m.model_dump() for m in a.messages] if a.messages else None,
                model=a.model,
                expected_output_tokens=a.expected_output_tokens,
            )
            for a in body.archetypes
        ]
        result = project(archetypes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.to_dict()


def _build_report_or_400(body: ReportRequest):
    try:
        return build_report(body.run_name, body.records)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/report")
def report_json(body: ReportRequest):
    """
    Consolidate a load-test run's per-request /analyze results into one
    report: totals, cost, a SAFE/WARNING/EXCEEDED breakdown, per-label
    and per-model and per-phase rollups, the peak single request, and
    the worst-offending flagged requests. Fully stateless — nothing is
    stored; submit every record you collected in one call, get one
    report back. See docs/INTEGRATION.md for the record shape and a
    worked example from any test suite.
    """
    return _build_report_or_400(body).to_dict()


@app.post("/report/html", response_class=HTMLResponse)
def report_html(body: ReportRequest):
    """Same report as POST /report, rendered as a single self-contained
    HTML page — for embedding in a viewer (e.g. an iframe) or opening
    directly in a browser."""
    report = _build_report_or_400(body)
    return HTMLResponse(content=render_report_html(report))


@app.post("/report/pdf")
def report_pdf(body: ReportRequest):
    """Same report, rendered to a downloadable PDF — the same HTML
    template as POST /report/html, converted via xhtml2pdf so the two
    always agree."""
    report = _build_report_or_400(body)
    html = render_report_html(report)

    from xhtml2pdf import pisa  # imported here: only /report/pdf needs it

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise HTTPException(status_code=500, detail="Could not render PDF for this report.")

    pdf_bytes = buffer.getvalue()
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in report.run_name).strip() or "airi-report"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# --- Source download (frontend/developers.html) ---
#
# The git repository is moving to restricted access, so this is the
# ongoing way for a developer to get AIRI's source: a zip built from
# exactly what's running on this deployment, not a separately
# maintained artifact that can drift out of sync.

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# Never shipped, even though most of these are also in .gitignore — this
# list is a hard safety net independent of git, since the zip is built
# from the live filesystem, not from a git checkout.
_DOWNLOAD_EXCLUDE_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}
_DOWNLOAD_EXCLUDE_FILES = {".env"}  # .env.example is fine and included
DOWNLOAD_CACHE_SECONDS = 300  # rebuild at most this often — a low-traffic convenience endpoint, not a hot path

_download_cache: Dict[str, Any] = {"bytes": None, "built_at": 0.0}


def _build_source_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
            dirnames[:] = sorted(d for d in dirnames if d not in _DOWNLOAD_EXCLUDE_DIRS)
            for filename in sorted(filenames):
                if filename in _DOWNLOAD_EXCLUDE_FILES or filename.endswith(".pyc"):
                    continue
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, _REPO_ROOT)
                zf.write(full_path, arcname=os.path.join("airi-source", rel_path))
    return buffer.getvalue()


@app.get("/download")
def download_source():
    """
    Zips up AIRI's own source (library, API, frontend, SQL migrations,
    docs) as it's currently deployed here, and serves it as an
    attachment. Deliberately unauthenticated — this is meant to be
    publicly downloadable, same spirit as the git repo it's replacing
    as AIRI's public distribution channel. Cached in memory for
    DOWNLOAD_CACHE_SECONDS so repeated downloads don't re-walk and
    re-zip the whole tree on every request.
    """
    now = time.monotonic()
    if _download_cache["bytes"] is None or (now - _download_cache["built_at"]) > DOWNLOAD_CACHE_SECONDS:
        _download_cache["bytes"] = _build_source_zip()
        _download_cache["built_at"] = now
    return Response(
        content=_download_cache["bytes"],
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="airi-source.zip"'},
    )


# Serve the try-it-out page at "/". Mounted last so it doesn't shadow the
# API routes above.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
