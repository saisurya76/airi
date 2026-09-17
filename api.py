"""
AIRI API — one focused endpoint on top of the core library, plus the
try-it-out page as a static file. Run it with:

    uvicorn api:app --reload

Then open http://127.0.0.1:8000/
"""

import io
import os
import py_compile
import secrets
import sys
import tempfile
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
from airi import author, comparison, consolidated_report, db, notes, runtime_config, tool_runs, vendor_pricing, workspaces as ws
from airi.analyzer import build_result_from_counts
from airi.auth import (
    CODE_TTL_SECONDS,
    MAX_VERIFY_ATTEMPTS,
    AuthError,
    create_admin_token,
    create_session_token,
    extract_bearer_token,
    generate_access_code,
    generate_code,
    hash_code,
    normalize_email,
    verify_access_code,
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


def _require_session_email_and_user_id(authorization: Optional[str]) -> Tuple[str, int]:
    """Resolves the session's email to a `users.id`, returning both — for
    the one caller (the Exact tool-run endpoint) that needs the email
    too, for rate limiting. A signed-in session with no matching user
    row shouldn't happen (verify-code always upserts one first) —
    treated as an expired/invalid session rather than a 500 if it
    somehow does."""
    email = _require_session_email(authorization)
    try:
        user_id = db.get_user_id_by_email(email)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if user_id is None:
        raise HTTPException(status_code=401, detail="Your session is no longer valid — sign in again.")
    return email, user_id


def _require_user_id(authorization: Optional[str]) -> int:
    _email, user_id = _require_session_email_and_user_id(authorization)
    return user_id


def _get_accessible_workspace(workspace_id: int, user_id: int) -> Tuple[dict, str]:
    """404 whether the workspace doesn't exist or the caller has no
    relationship to it at all (not the owner, no membership row) — never
    confirms another workspace id exists to someone with no relationship
    to it. 403 if the caller has a *disabled* membership — a real
    relationship, just not an active one right now, so it's fine to say
    so rather than pretending the workspace doesn't exist.

    Returns (workspace, role): role is "admin" for the creator, "member"
    for an active team member (see sql/006_workspace_member_access.sql)."""
    try:
        workspace = db.get_workspace(workspace_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if workspace["owner_user_id"] == user_id:
        return workspace, "admin"
    try:
        membership = db.get_membership(workspace_id, user_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    if membership["status"] != "active":
        raise HTTPException(status_code=403, detail="Your access to this workspace has been disabled by its admin.")
    return workspace, "member"


def _require_workspace_admin(workspace_id: int, user_id: int) -> dict:
    """Gate for admin-only workspace actions: update basic details,
    delete the workspace, create/delete a project, and all membership
    management (add/remove/disable/enable/regenerate-code). An active
    team member reaches here (403), not just an outsider (404) — see
    _get_accessible_workspace.

    Distinct on purpose from _require_admin (above), which gates the
    site's password-protected founder admin page — an unrelated concept
    that happens to share the word "admin"; never use one where the
    other belongs."""
    workspace, role = _get_accessible_workspace(workspace_id, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only the workspace admin can do that.")
    return workspace


def _get_accessible_project(project_id: int, user_id: int) -> Tuple[dict, dict, str]:
    """Returns (project, workspace, role) after confirming the caller can
    reach the project's parent workspace — same 404/403 reasoning as
    _get_accessible_workspace."""
    try:
        proj = db.get_project(project_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if proj is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    workspace, role = _get_accessible_workspace(proj["workspace_id"], user_id)
    return proj, workspace, role


def _require_project_admin(project_id: int, user_id: int) -> Tuple[dict, dict]:
    """Gate for admin-only project actions: update basic details, delete."""
    proj, workspace, role = _get_accessible_project(project_id, user_id)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only the workspace admin can do that.")
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


class MemberLoginBody(BaseModel):
    """A team member's own sign-in — see POST /auth/member-login. Distinct
    from VerifyCodeBody: that's step 2 of the owner's one-time-emailed-OTP
    flow, this is a member's persistent access code, issued once by their
    workspace admin and reused every time they sign in."""

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


class AdminVisibilityBody(BaseModel):
    """Full replace, same semantics as AdminConfigBody/AuthorProfileBody —
    the admin page always sends all three current toggle states together,
    so a field left out here is never accidentally implied by omission."""

    show_author_link: bool = True
    show_license_wizard: bool = True
    show_sdlc_wizard: bool = True


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


class NoteBody(BaseModel):
    body: str = ""


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


# --- Saved tool runs inside a project (Phase 2 — see docs/WORKSPACES.md) ---
#
# Same request shape as the existing stateless endpoint in each case,
# plus one new optional field: a label for this particular saved run
# (distinct from ReportRequest.records[].label, which labels one record
# inside a report, not the run itself).

class AnalyzeRunBody(AnalyzeRequest):
    label: str = ""


class ExactRunBody(ExactAnalyzeRequest):
    label: str = ""


class ProjectRunBody(ProjectRequest):
    label: str = ""


class ReportRunBody(ReportRequest):
    label: str = ""


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/config")
def public_config():
    """Public, unauthenticated: just enough for the frontend to decide
    what to show. test_mode drives the Exact flow — a BYOK key panel
    (test_mode: false) or a "you're using AIRI's testing keys" note
    (test_mode: true). The show_* fields are the admin-settable site
    visibility toggles (see runtime_config.py) — every page that links to
    the Author profile or the two newer demo wizards checks these and
    hides that link when its flag is false. Never exposes whether any
    secret is actually configured — that's only in GET /admin/config,
    behind the admin password."""
    test_mode, _source = runtime_config.get_test_mode()
    return {"test_mode": test_mode, **runtime_config.get_all_visibility()}


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


def _vendor_tool_dict(tool: "vendor_pricing.VendorTool") -> dict:
    return {
        "id": tool.id,
        "name": tool.name,
        "vendor": tool.vendor,
        "category": tool.category,
        "billing_unit": tool.billing_unit,
        "pricing_url": tool.pricing_url,
        "note": tool.note,
    }


@app.get("/pricing/license-tools")
def pricing_license_tools():
    """Vendor list for the License Requests wizard's dropdown — utility/
    SaaS AI seat licenses (Microsoft 365 Copilot, Cowork, ChatGPT
    Enterprise, ...). No prices here; see GET /pricing/{tool_id}/live."""
    return [_vendor_tool_dict(t) for t in vendor_pricing.list_license_tools()]


@app.get("/pricing/sdlc-tools")
def pricing_sdlc_tools():
    """Vendor list for the SDLC Tools wizard's dropdown — developer-tool AI
    seats and usage (GitHub Copilot, Microsoft Foundry, Cursor, ...)."""
    return [_vendor_tool_dict(t) for t in vendor_pricing.list_sdlc_tools()]


@app.get("/pricing/{tool_id}/live")
def pricing_live(tool_id: str):
    """Best-effort: fetches the vendor's own public pricing page right now
    and tries to read a per-seat USD price off it. This is intentionally
    not a cached static table (vendor pricing changes and varies by
    tier/region/negotiated discount) — a miss here is expected for a good
    chunk of vendors (JS-rendered pages, quote-only pricing) and is a
    normal 404, not a server error; the frontend always falls back to
    letting the person type in their own known price."""
    tool = vendor_pricing.get_tool(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Unknown tool.")
    try:
        return vendor_pricing.fetch_live_price(tool_id)
    except vendor_pricing.PricingFetchError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _analyze_input_for_storage(body: "AnalyzeRequest") -> Dict[str, Any]:
    """The plain-dict shape of an /analyze-style request, safe to persist
    as-is (used both for /analyze and /analyze/exact — see
    _exact_input_for_storage for why Exact needs its own variant)."""
    return {
        "prompt": body.prompt,
        "messages": [m.model_dump() for m in body.messages] if body.messages else None,
        "model": body.model,
        "expected_output_tokens": body.expected_output_tokens,
    }


def _do_analyze(body: "AnalyzeRequest") -> dict:
    """Shared by POST /analyze and POST /projects/{id}/tools/analyze/runs
    — one code path so a saved run and a plain /analyze call can never
    silently drift apart."""
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


@app.post("/analyze")
def analyze_request(body: AnalyzeRequest):
    return _do_analyze(body)


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
    user_id = db.upsert_user_login(email)
    terms_accepted = db.get_terms_accepted_at(user_id) is not None
    return {"token": create_session_token(email, secret), "email": email, "terms_accepted": terms_accepted}


@app.post("/auth/member-login")
def member_login(body: MemberLoginBody):
    """Sign-in for a team member, using the persistent access code their
    workspace admin gave them when adding them (see POST
    /workspaces/{id}/members) — a separate credential and a separate
    endpoint from the owner's email+OTP flow above, but it produces an
    identical, fully-capable session token via the same
    create_session_token: there's no separate "session type" to track,
    because every workspace/project endpoint evaluates permission
    per-request from the workspace_members row for this session's
    resolved user_id (see _get_accessible_workspace), independent of
    which login path produced the session.

    A member can be added to more than one workspace, each with its own
    code, so this checks the submitted code against every active
    membership for the email rather than assuming a single one."""
    secret = _get_auth_secret()
    try:
        email = normalize_email(body.email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        memberships = db.get_active_memberships_by_email(email)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    matched = any(
        verify_access_code(email, body.code, secret, m["access_code_hash"])
        for m in memberships
    )
    if not matched:
        raise HTTPException(status_code=400, detail="Incorrect email or access code.")

    user_id = db.upsert_user_login(email)
    terms_accepted = db.get_terms_accepted_at(user_id) is not None
    return {"token": create_session_token(email, secret), "email": email, "terms_accepted": terms_accepted}


@app.get("/auth/me")
def auth_me(authorization: Optional[str] = Header(default=None)):
    """Lets the frontend check whether a stored token is still valid on
    page load, without re-doing the OTP flow. Also reports whether this
    account has accepted the current Terms & Conditions yet, so a page
    that resumes an existing session (rather than just having completed
    one of the two login endpoints above) can still gate on it."""
    secret = _get_auth_secret()
    try:
        email = verify_session_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    terms_accepted = False
    try:
        user_id = db.get_user_id_by_email(email)
        if user_id is not None:
            terms_accepted = db.get_terms_accepted_at(user_id) is not None
    except db.DatabaseNotConfigured:
        pass
    return {"email": email, "terms_accepted": terms_accepted}


@app.post("/auth/accept-terms")
def accept_terms_endpoint(authorization: Optional[str] = Header(default=None)):
    """Records that the signed-in user has accepted the current Terms &
    Conditions — called once, right after the frontend's accept-terms
    gate, before it lets them into the signed-in app for the first time."""
    user_id = _require_user_id(authorization)
    try:
        db.accept_terms(user_id)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"terms_accepted": True}


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


def _workspace_detail(workspace: dict, role: str) -> dict:
    """A workspace plus its members, projects, and the caller's own role
    ("admin" or "member") — everything requirement #10 ("when he logs in
    all the above details must be shown back") needs for one workspace in
    one call, plus what the frontend needs to know which controls to
    show a non-admin team member."""
    workspace_id = workspace["id"]
    return {
        **workspace,
        "role": role,
        "members": db.list_workspace_members(workspace_id),
        "projects": db.list_projects(workspace_id),
    }


@app.get("/workspaces")
def list_workspaces(authorization: Optional[str] = Header(default=None)):
    """Every workspace this signed-in user can reach — the ones they own
    plus the ones where they're an active team member — most recent
    first, each annotated with the caller's role and member/project
    counts. The workspace-list view."""
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
    return _workspace_detail(workspace, "admin")  # whoever creates a workspace is its admin


@app.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    workspace, role = _get_accessible_workspace(workspace_id, user_id)
    return _workspace_detail(workspace, role)


@app.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: int, body: WorkspaceBody, authorization: Optional[str] = Header(default=None)):
    """Editing a workspace's basic details (title/target/description) is
    admin-only — a team member can work inside a workspace but can't
    rename it or change what it's for."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    try:
        title, target, description = ws.validate_workspace_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    workspace = db.update_workspace(workspace_id, title, target, description)
    return _workspace_detail(workspace, "admin")


@app.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    """Deleting a workspace cascades to its members and projects (see
    sql/003_workspaces_schema.sql's ON DELETE CASCADE) — the app-key
    confirmation exists specifically because this one action can take
    an entire workspace's projects with it. Admin-only: a team member
    can never delete the workspace they were added to."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_workspace(workspace_id)
    return {"deleted": True}


@app.post("/workspaces/{workspace_id}/members")
def add_workspace_member(workspace_id: int, body: WorkspaceMemberBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: adds a team member by email, generates their access
    code (their ongoing login credential — see POST /auth/member-login),
    stores only its hash, and resolves/creates the member's own `users`
    row right away (via upsert_user_login) so permission checks have a
    user_id to key off of even before the member ever signs in.

    The plaintext code is returned exactly once, in this response — it
    is never stored, logged, or emailed with the code embedded (the
    notification below is a courtesy that a member was added, not a
    credential delivery — the admin relays the code out-of-band)."""
    user_id = _require_user_id(authorization)
    workspace = _require_workspace_admin(workspace_id, user_id)
    try:
        email = ws.normalize_member_email(body.email)
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    pepper = _get_auth_secret()
    try:
        member_user_id = db.upsert_user_login(email)
        code = generate_access_code()
        member = db.add_workspace_member(workspace_id, email, member_user_id, hash_code(email, code, pepper))
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if member is None:
        raise HTTPException(status_code=409, detail=f"{email} is already a member of this workspace.")

    try:
        send_member_added_email(email, workspace["title"])
    except EmailSendError:
        pass  # membership is saved either way; the email is a courtesy

    return {**member, "access_code": code}


@app.delete("/workspaces/{workspace_id}/members/{member_id}")
def remove_workspace_member(workspace_id: int, member_id: int, authorization: Optional[str] = Header(default=None)):
    """Admin-only. A hard delete — see set_workspace_member_status's
    disable/enable endpoints below for the reversible alternative."""
    user_id = _require_user_id(authorization)
    workspace = _require_workspace_admin(workspace_id, user_id)
    member = db.remove_workspace_member(workspace_id, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found.")

    try:
        send_member_removed_email(member["email"], workspace["title"])
    except EmailSendError:
        pass

    return {"deleted": True}


@app.post("/workspaces/{workspace_id}/members/{member_id}/disable")
def disable_workspace_member(workspace_id: int, member_id: int, authorization: Optional[str] = Header(default=None)):
    """Admin-only. Revokes the member's access without removing them or
    their history — distinct from remove_workspace_member's hard delete.
    A disabled member's existing session tokens (if any) stay
    cryptographically valid until they expire, but every workspace/
    project endpoint re-checks membership status on every request (see
    _get_accessible_workspace), so a disabled member is locked out
    immediately regardless of token expiry."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    member = db.set_workspace_member_status(workspace_id, member_id, "disabled")
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found.")
    return member


@app.post("/workspaces/{workspace_id}/members/{member_id}/enable")
def enable_workspace_member(workspace_id: int, member_id: int, authorization: Optional[str] = Header(default=None)):
    """Admin-only. Restores a previously disabled member's access using
    their existing code — no need to re-add them or issue a new one."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    member = db.set_workspace_member_status(workspace_id, member_id, "active")
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found.")
    return member


@app.post("/workspaces/{workspace_id}/members/{member_id}/regenerate-code")
def regenerate_workspace_member_code(workspace_id: int, member_id: int, authorization: Optional[str] = Header(default=None)):
    """Admin-only. Issues a brand-new access code for a member (e.g. a
    lost or compromised one) and invalidates the old one immediately —
    since only the hash was ever stored, there's no way to recover the
    old code to invalidate it any other way. Returned exactly once, same
    as at member-creation time."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    member = db.get_workspace_member(workspace_id, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Team member not found.")
    pepper = _get_auth_secret()
    code = generate_access_code()
    updated = db.regenerate_workspace_member_code(workspace_id, member_id, hash_code(member["email"], code, pepper))
    return {**updated, "access_code": code}


@app.get("/projects/tech-stack-categories")
def tech_stack_categories():
    """The tech-stack form's category list (label + required/optional),
    so the frontend never has to hardcode it separately from
    airi/workspaces.py — add a category there and it shows up here."""
    return ws.TECH_STACK_CATEGORIES


@app.post("/workspaces/{workspace_id}/projects")
def create_project(workspace_id: int, body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: creating a project is a workspace-identity operation,
    same bucket as creating/deleting the workspace itself — a team
    member works inside existing projects but can't add new ones."""
    user_id = _require_user_id(authorization)
    _require_workspace_admin(workspace_id, user_id)
    try:
        title, description, tech_stack = ws.validate_project_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**db.create_project(workspace_id, title, description, tech_stack), "role": "admin"}


@app.get("/projects/{project_id}")
def get_project(project_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    project_row, _workspace, role = _get_accessible_project(project_id, user_id)
    return {**project_row, "role": role}


@app.put("/projects/{project_id}")
def update_project(project_id: int, body: ProjectBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: a team member can't rename a project or change its
    tech stack, only work inside it (run tools, add notes)."""
    user_id = _require_user_id(authorization)
    _require_project_admin(project_id, user_id)
    try:
        title, description, tech_stack = ws.validate_project_fields(body.model_dump())
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {**db.update_project(project_id, title, description, tech_stack), "role": "admin"}


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only, same reasoning as delete_workspace."""
    user_id = _require_user_id(authorization)
    _require_project_admin(project_id, user_id)
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_project(project_id)
    return {"deleted": True}


# --- Saved tool runs inside a project (Phase 2 — see docs/WORKSPACES.md) ---
#
# Each of AIRI's four existing tools (Standard analyze, Exact mode,
# traffic projection, load-test report) becomes runnable *and saved*
# inside a project — same request/response shape as the original
# stateless endpoint (via the _do_analyze/_do_exact/_do_project/
# _build_report_or_400 helpers above and below), plus persistence.
# `_do_exact` and `_resolve_exact_api_key` are defined further down,
# right before /analyze/exact — Python resolves names inside a function
# body at call time, so the forward reference is fine.


@app.post("/projects/{project_id}/tools/analyze/runs")
def create_analyze_run(project_id: int, body: AnalyzeRunBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    try:
        label = tool_runs.validate_label(body.label)
    except tool_runs.ToolRunError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = _do_analyze(body)
    return db.create_tool_run(project_id, tool_runs.ToolName.analyze.value, label, _analyze_input_for_storage(body), result)


@app.post("/projects/{project_id}/tools/exact/runs")
def create_exact_run(project_id: int, body: ExactRunBody, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    _check_exact_rate_limit(email)
    try:
        label = tool_runs.validate_label(body.label)
    except tool_runs.ToolRunError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    test_mode, _source = runtime_config.get_test_mode()
    result = _do_exact(body, test_mode)
    # Never persists anthropic_api_key/google_api_key even if this
    # request carried a BYOK key — see _exact_input_for_storage.
    return db.create_tool_run(project_id, tool_runs.ToolName.exact.value, label, _exact_input_for_storage(body), result)


@app.post("/projects/{project_id}/tools/project/runs")
def create_project_run(project_id: int, body: ProjectRunBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    try:
        label = tool_runs.validate_label(body.label)
    except tool_runs.ToolRunError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = _do_project(body)
    return db.create_tool_run(project_id, tool_runs.ToolName.project.value, label, _project_input_for_storage(body), result)


@app.post("/projects/{project_id}/tools/report/runs")
def create_report_run(project_id: int, body: ReportRunBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    try:
        label = tool_runs.validate_label(body.label)
    except tool_runs.ToolRunError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    report = _build_report_or_400(body)
    input_data = {"run_name": body.run_name, "records": body.records}
    return db.create_tool_run(project_id, tool_runs.ToolName.report.value, label, input_data, report.to_dict())


@app.get("/projects/{project_id}/tools/{tool}/runs")
def list_project_tool_runs(project_id: int, tool: tool_runs.ToolName, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    return db.list_tool_runs(project_id, tool.value)


@app.delete("/projects/{project_id}/tools/{tool}/runs/{run_id}")
def delete_project_tool_run(project_id: int, tool: tool_runs.ToolName, run_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    run = db.get_tool_run(run_id)
    if run is None or run["project_id"] != project_id or run["tool"] != tool.value:
        raise HTTPException(status_code=404, detail="Tool run not found.")
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_tool_run(run_id)
    return {"deleted": True}


# --- Notes / comments history inside a project (Phase 3) ---
#
# An append-only history, not an editable document: the only mutation is
# delete (app-key gated, same as everywhere else in this feature). See
# docs/WORKSPACES.md and airi/notes.py.


@app.post("/projects/{project_id}/notes")
def create_project_note(project_id: int, body: NoteBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    try:
        note_body = notes.validate_note_body(body.body)
    except notes.NoteError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return db.create_note(project_id, note_body)


@app.get("/projects/{project_id}/notes")
def list_project_notes(project_id: int, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    return db.list_notes(project_id)


@app.delete("/projects/{project_id}/notes/{note_id}")
def delete_project_note(project_id: int, note_id: int, body: AppKeyConfirmBody, authorization: Optional[str] = Header(default=None)):
    user_id = _require_user_id(authorization)
    _get_accessible_project(project_id, user_id)
    note = db.get_note(note_id)
    if note is None or note["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Note not found.")
    _require_app_key_confirmed(user_id, body.app_key)
    db.delete_note(note_id)
    return {"deleted": True}


# --- Dashboard + consolidated report (Phase 3) ---
#
# One aggregation function backs both the Actions tab's on-screen JSON
# summary and its downloadable PDF, so the two can never silently
# disagree — same reasoning as _build_report_or_400 / render_report_html
# for the standalone Load-test report. See airi/consolidated_report.py.
#
# "Prepared by": today, the only user who can ever reach a project is the
# workspace's owner (there's no real team collaboration yet — members are
# notified by email but don't get their own access, see
# docs/WORKSPACES.md) — so the signed-in session's own email *is* the
# project's creator. This will need a real per-project creator lookup
# once team collaboration ships in a later phase.


def _build_consolidated_report(project_id: int, user_id: int, email: str) -> Dict[str, Any]:
    proj, _workspace, _role = _get_accessible_project(project_id, user_id)
    runs_by_tool = {t.value: db.list_tool_runs(project_id, t.value) for t in tool_runs.ToolName}
    all_runs = [r for runs in runs_by_tool.values() for r in runs]
    totals = consolidated_report.aggregate_totals(all_runs)
    by_tool = consolidated_report.aggregate_by_tool(runs_by_tool)
    latest = consolidated_report.latest_per_tool(runs_by_tool)
    return {
        "project": proj,
        "prepared_by": email,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": totals,
        "by_tool": by_tool,
        "latest": latest,
        "notes": db.list_notes(project_id),
    }


@app.get("/projects/{project_id}/report/consolidated")
def get_consolidated_report(project_id: int, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    return _build_consolidated_report(project_id, user_id, email)


@app.get("/projects/{project_id}/report/consolidated.pdf")
def get_consolidated_report_pdf(project_id: int, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    data = _build_consolidated_report(project_id, user_id, email)

    html = consolidated_report.render_consolidated_report_html(
        data["project"], data["prepared_by"], data["generated_at"],
        data["totals"], data["by_tool"], data["latest"], data["notes"],
    )

    from xhtml2pdf import pisa  # imported here: only this endpoint needs it (matches /report/pdf)

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise HTTPException(status_code=500, detail="Could not render the consolidated report PDF.")

    pdf_bytes = buffer.getvalue()
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in data["project"]["title"]).strip() or "airi-project"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}-consolidated-report.pdf"'},
    )


# --- Download a single saved tool run as its own PDF (Phase 4) ---
#
# The per-project consolidated report (above) already covers "every
# tab's findings in one document" — this is the narrower "just this one
# result" download the original spec also asked for, on each tool tab.


@app.get("/projects/{project_id}/tools/{tool}/runs/{run_id}/pdf")
def get_tool_run_pdf(project_id: int, tool: tool_runs.ToolName, run_id: int, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    proj, _workspace, _role = _get_accessible_project(project_id, user_id)
    run = db.get_tool_run(run_id)
    if run is None or run["project_id"] != project_id or run["tool"] != tool.value:
        raise HTTPException(status_code=404, detail="Tool run not found.")

    html = consolidated_report.render_single_run_html(proj, email, run)

    from xhtml2pdf import pisa  # imported here: only this endpoint needs it (matches /report/pdf)

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise HTTPException(status_code=500, detail="Could not render this run's PDF.")

    pdf_bytes = buffer.getvalue()
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in f"{proj['title']}-{tool.value}").strip() or "airi-run"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'},
    )


# --- Cross-project comparison (Phase 4) ---
#
# Once a workspace has 2+ projects, this rolls each project's saved-run
# history (same normalization as the consolidated report, one level up)
# into a side-by-side comparison, ranked by estimated cost. Like the
# consolidated report, one aggregation backs both the on-screen JSON and
# the PDF — see airi/comparison.py.


def _build_workspace_comparison(workspace_id: int, user_id: int, email: str) -> Dict[str, Any]:
    workspace, _role = _get_accessible_workspace(workspace_id, user_id)
    projects = db.list_projects(workspace_id)
    summaries = []
    for proj in projects:
        runs_by_tool = {t.value: db.list_tool_runs(proj["id"], t.value) for t in tool_runs.ToolName}
        summaries.append(comparison.project_summary(proj, runs_by_tool))
    ranked = comparison.rank_by_cost(summaries)
    workspace_totals = comparison.aggregate_workspace_totals(ranked)
    return {
        "workspace": workspace,
        "prepared_by": email,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "workspace_totals": workspace_totals,
        "projects": ranked,
    }


@app.get("/workspaces/{workspace_id}/comparison")
def get_workspace_comparison(workspace_id: int, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    return _build_workspace_comparison(workspace_id, user_id, email)


@app.get("/workspaces/{workspace_id}/comparison.pdf")
def get_workspace_comparison_pdf(workspace_id: int, authorization: Optional[str] = Header(default=None)):
    email, user_id = _require_session_email_and_user_id(authorization)
    data = _build_workspace_comparison(workspace_id, user_id, email)

    html = comparison.render_comparison_report_html(
        data["workspace"], data["prepared_by"], data["generated_at"],
        data["workspace_totals"], data["projects"],
    )

    from xhtml2pdf import pisa  # imported here: only this endpoint needs it (matches /report/pdf)

    buffer = io.BytesIO()
    result = pisa.CreatePDF(src=html, dest=buffer)
    if result.err:
        raise HTTPException(status_code=500, detail="Could not render the comparison report PDF.")

    pdf_bytes = buffer.getvalue()
    filename = "".join(c if c.isalnum() or c in "-_ " else "_" for c in data["workspace"]["title"]).strip() or "airi-workspace"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}-comparison-report.pdf"'},
    )


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
        "visibility": runtime_config.get_all_visibility(),
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


@app.post("/admin/visibility")
def admin_set_visibility(body: AdminVisibilityBody, authorization: Optional[str] = Header(default=None)):
    """Admin-only: sets the three site visibility toggles (Author page
    link, License wizard, SDLC wizard) in one call — the admin page's
    "Site visibility" panel always sends all three current switch states
    together, same full-replace convention as /admin/config and
    /admin/author. See runtime_config.py for what hiding a flag actually
    does on the frontend."""
    _require_admin(authorization)
    try:
        runtime_config.set_visibility("show_author_link", body.show_author_link)
        runtime_config.set_visibility("show_license_wizard", body.show_license_wizard)
        runtime_config.set_visibility("show_sdlc_wizard", body.show_sdlc_wizard)
    except db.DatabaseNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return runtime_config.get_all_visibility()


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


def _exact_input_for_storage(body: "ExactAnalyzeRequest") -> Dict[str, Any]:
    """Same shape as _analyze_input_for_storage but deliberately does NOT
    include anthropic_api_key/google_api_key — a BYOK key must never be
    written to the database, even inside a saved tool-run's `input`."""
    return _analyze_input_for_storage(body)


def _do_exact(body: "ExactAnalyzeRequest", test_mode: bool) -> dict:
    """Shared by POST /analyze/exact and POST /projects/{id}/tools/exact/runs
    — everything after auth + rate limiting, which each caller still does
    itself (the per-project endpoint needs its own ownership check first
    anyway, so there's no single shared auth wrapper for this one)."""
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
    email = _require_session_email(authorization)
    _check_exact_rate_limit(email)
    test_mode, _source = runtime_config.get_test_mode()
    return _do_exact(body, test_mode)


def _project_input_for_storage(body: "ProjectRequest") -> Dict[str, Any]:
    return {"archetypes": [a.model_dump() for a in body.archetypes]}


def _do_project(body: "ProjectRequest") -> dict:
    """Shared by POST /project and POST /projects/{id}/tools/project/runs."""
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
    return _do_project(body)


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


# --- Binary download (frontend/developers.html) ---
#
# The public download used to be a zip of the entire repository
# (library + API + frontend + SQL migrations + docs). That's no longer
# what's offered: this is now a *compiled* build of just AIRI's core
# estimation library — the small, zero-web/db/cloud-dependency module
# set behind `from airi import analyze` (see README.md's project
# layout for the exact "core library" vs. "API-layer only" split).
# Nothing else — not api.py, not frontend/, not sql/, not docs/, not
# the API-layer-only airi/ modules (auth.py, db.py, workspaces.py,
# etc.) — is ever included. This is an explicit allowlist (not a
# denylist over the whole tree, which is what the old version did), so
# a new file added anywhere in the repo is excluded by default rather
# than shipped by accident.
#
# "Compiled" means exactly that: each module below is compiled to
# Python bytecode (.pyc) and shipped *without* its .py source — a
# sourceless distribution, which CPython's import machinery loads
# natively with no extra tooling. This is why frontend/developers.html
# calls out the exact CPython version required: the bytecode is tied
# to whichever interpreter this server is running (see
# PY_BINARY_VERSION below), not just any Python 3.
#
# Rebuilt from whatever's on disk at cache-expiry time (or on first
# request after a restart), the same as the old source zip was — so a
# new deploy of the core library is reflected here automatically, with
# no separate publish step.

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# The exact "core library" file set — see README.md's project layout.
# Deliberately a hardcoded allowlist: only these files, from airi/ only.
_CORE_LIBRARY_FILES = [
    "__init__.py",
    "models.py",
    "registry.py",
    "pricing.py",
    "tokenizer.py",
    "analyzer.py",
    "projector.py",
    "report.py",
    "report_render.py",
]
PY_BINARY_VERSION = "%d.%d" % (sys.version_info.major, sys.version_info.minor)
DOWNLOAD_CACHE_SECONDS = 300  # rebuild at most this often — a low-traffic convenience endpoint, not a hot path

_download_cache: Dict[str, Any] = {"bytes": None, "built_at": 0.0}

_BINARY_README = """AIRI -- compiled core library (Python {version})
=================================================

This is a compiled (bytecode-only) build of AIRI's core token/cost
estimation library -- no source code included. It's rebuilt from
whatever's currently deployed every time it's downloaded, so it always
matches the live service.

Usage -- unzip, then either drop the airi/ folder next to your script
or add it to PYTHONPATH:

    from airi import analyze, project, build_report

    result = analyze(prompt="Explain quantum computing simply.",
                      model="gpt-4o", expected_output_tokens=500)
    print(result.to_dict())

Requirements:
  - CPython {version}.x specifically -- compiled bytecode is tied to the
    exact interpreter version that produced it. A different Python 3
    minor version will raise "bad magic number" on import.
  - No other dependencies for analyze()/project(). build_report()'s
    HTML rendering needs no extra dependency either (stdlib only);
    turning that HTML into a PDF (as AIRI's own /report/pdf does) needs
    xhtml2pdf, which is not bundled here.

Not included: the API server, the frontend, SQL migrations, docs, or
any of AIRI's account/auth/workspace features -- those aren't
distributed as source. For everything else, use the hosted HTTP API
directly (see the Developers page) rather than self-hosting.
""".format(version=PY_BINARY_VERSION)


def _build_binary_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("airi-binary/README.txt", _BINARY_README)
        with tempfile.TemporaryDirectory() as tmp_dir:
            for filename in _CORE_LIBRARY_FILES:
                src_path = os.path.join(_REPO_ROOT, "airi", filename)
                pyc_path = os.path.join(tmp_dir, filename + "c")  # foo.py -> foo.pyc
                py_compile.compile(src_path, cfile=pyc_path, dfile=filename, doraise=True)
                zf.write(pyc_path, arcname=os.path.join("airi-binary", "airi", filename + "c"))
    return buffer.getvalue()


@app.get("/download")
def download_binary():
    """
    Serves a compiled (sourceless bytecode) build of AIRI's core
    library as a zip attachment -- see the module-level comment above
    for exactly what is and isn't included. Deliberately unauthenticated
    and publicly downloadable. Cached in memory for DOWNLOAD_CACHE_SECONDS
    so repeated downloads don't recompile on every request.
    """
    now = time.monotonic()
    if _download_cache["bytes"] is None or (now - _download_cache["built_at"]) > DOWNLOAD_CACHE_SECONDS:
        _download_cache["bytes"] = _build_binary_zip()
        _download_cache["built_at"] = now
    return Response(
        content=_download_cache["bytes"],
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="airi-binary.zip"'},
    )


# Serve the try-it-out page at "/". Mounted last so it doesn't shadow the
# API routes above.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
