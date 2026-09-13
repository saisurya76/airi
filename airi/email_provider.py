"""
Sends the OTP code email via Resend's HTTP API. A deliberately thin
wrapper — one POST, no SDK dependency — since that's all Resend's API
actually is.

Requires a verified sending domain in Resend (their sandbox domain can
only deliver to the Resend account's own address, not to real users) —
see docs/EXACT_MODE.md for the one-time setup. Two env vars:

    RESEND_API_KEY    - from the Resend dashboard
    RESEND_FROM_EMAIL - e.g. "AIRI <otp@mail.yourdomain.com>", must be
                        on a domain you've verified in Resend

Like report_render.py and exact_provider.py, this is an API-layer
concern (makes a real network call) and is never imported by
airi/__init__.py — the core stays exactly as network-optional as before.
"""

import os

import httpx

RESEND_API_URL = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0


class EmailSendError(RuntimeError):
    """Raised when Resend can't be reached or rejects the send — the API
    layer turns this into a safe-to-show error rather than a raw traceback."""


def _subject_and_body(code: str) -> tuple:
    subject = f"{code} is your AIRI verification code"
    text = (
        f"Your AIRI verification code is: {code}\n\n"
        "This code expires in 10 minutes and can only be used once.\n\n"
        "If you didn't request this, you can safely ignore this email."
    )
    html = f"""\
<div style="font-family:Helvetica,Arial,sans-serif;max-width:420px;margin:0 auto;padding:32px 24px;">
  <p style="color:#6b7280;font-size:12px;font-weight:700;letter-spacing:0.08em;margin:0 0 16px;">AIRI &mdash; AI REQUEST INTELLIGENCE</p>
  <p style="font-size:15px;color:#1a1a1a;margin:0 0 20px;">Your verification code:</p>
  <p style="font-size:32px;font-weight:700;letter-spacing:0.12em;color:#14213d;margin:0 0 20px;">{code}</p>
  <p style="font-size:13px;color:#6b7280;margin:0;">Expires in 10 minutes, single use. Didn't request this? Ignore this email.</p>
</div>"""
    return subject, text, html


def send_otp_email(to_email: str, code: str) -> None:
    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("RESEND_FROM_EMAIL")
    if not api_key or not from_email:
        raise EmailSendError("Email sign-in isn't configured on this deployment yet.")

    subject, text, html = _subject_and_body(code)

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": from_email, "to": [to_email], "subject": subject, "text": text, "html": html},
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise EmailSendError(f"Could not reach the email provider: {exc}") from exc

    if resp.status_code >= 400:
        # Resend's error body is JSON like {"message": "...", "name": "..."}
        # — surface the message but never the API key or raw headers.
        detail = "the email provider rejected the request"
        try:
            detail = resp.json().get("message", detail)
        except Exception:
            pass
        raise EmailSendError(f"Could not send the code: {detail}")
