"""
AIRI API — one focused endpoint on top of the core library, plus the
try-it-out page as a static file. Run it with:

    uvicorn api:app --reload

Then open http://127.0.0.1:8000/
"""

import io
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from airi import Archetype, analyze, build_report, list_supported_models, project
from airi import db
from airi.analyzer import build_result_from_counts
from airi.auth import (
    CODE_TTL_SECONDS,
    MAX_VERIFY_ATTEMPTS,
    AuthError,
    create_session_token,
    extract_bearer_token,
    generate_code,
    hash_code,
    normalize_email,
    verify_code,
    verify_session_token,
)
from airi.email_provider import EmailSendError, send_otp_email
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
    return {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "google": os.environ.get("GOOGLE_API_KEY"),
    }.get(provider)


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


@app.post("/analyze/exact")
def analyze_exact(body: AnalyzeRequest, authorization: Optional[str] = Header(default=None)):
    """
    Same request/response shape as /analyze, but for Anthropic/Google
    models it calls that provider's own (free, no-charge) token-counting
    API instead of the chars/4 heuristic — see airi/exact_provider.py.
    OpenAI models are already exact via tiktoken locally, so this just
    delegates to the same path /analyze uses for those.

    Requires a signed-in session (`Authorization: Bearer <token>` from
    /auth/verify-code) — Exact mode calls out using AIRI's own shared
    provider API keys, not a per-user key, so sign-in exists to keep that
    shared quota from being hammered by anonymous traffic, not to gate a
    paid feature.
    """
    secret = _get_auth_secret()
    try:
        email = verify_session_token(extract_bearer_token(authorization), secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    _check_exact_rate_limit(email)

    messages = [m.model_dump() for m in body.messages] if body.messages else None
    spec = get_model_spec(body.model)

    if spec.tokenizer_family == "openai" or not has_exact_provider(spec.provider):
        # tiktoken already gives an exact count locally for OpenAI models —
        # no provider call needed. For a model with no exact_provider
        # integration (unknown/unsupported), this is the same heuristic
        # /analyze would give; Exact mode has nothing more to offer it.
        result = analyze(prompt=body.prompt, messages=messages, model=body.model, expected_output_tokens=body.expected_output_tokens)
        return result.to_dict()

    api_key = _get_provider_api_key(spec.provider)
    if not api_key:
        raise HTTPException(status_code=503, detail=f"Exact mode for {spec.provider} models isn't configured on this deployment yet.")

    try:
        input_tokens = count_tokens_exact(
            prompt=body.prompt, messages=messages, model=body.model, provider=spec.provider, api_key=api_key,
        )
    except ExactCountUnavailable as exc:
        # Graceful degrade to the heuristic, same spirit as tiktoken's own
        # network fallback — but say so honestly rather than silently
        # returning a heuristic number under an "Exact" label.
        result = analyze(prompt=body.prompt, messages=messages, model=body.model, expected_output_tokens=body.expected_output_tokens)
        data = result.to_dict()
        data["exact_mode_note"] = f"Exact provider count unavailable right now ({exc}) — showing the heuristic estimate instead."
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


# Serve the try-it-out page at "/". Mounted last so it doesn't shadow the
# API routes above.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
