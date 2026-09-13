"""
Email+OTP auth for AIRI's "Exact" flavor — deliberately minimal: no
passwords, no phone, no session table. A logged-in session is a signed
JWT, so verifying one costs zero database round trips.

Pure logic only (code generation, hashing, JWT encode/decode) — no
network calls, no DB access, so it's fully unit-testable without Neon,
Resend, or a real Postgres connection. Database access lives in
airi/db.py; sending the email lives in airi/email_provider.py; wiring
it all together into endpoints lives in api.py.

This module (like report_render.py and exact_provider.py) is an
API-layer concern: it's part of the `airi` package for reuse, but is
never imported by airi/__init__.py — the core analyze()/project()/
build_report() path stays exactly as stateless and auth-free as before.
"""

import hashlib
import re
import secrets
import time
from typing import Optional

import jwt as _pyjwt

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

CODE_LENGTH = 6
CODE_TTL_SECONDS = 10 * 60          # a requested code is valid for 10 minutes
MAX_VERIFY_ATTEMPTS = 5             # per code, before it's dead regardless of TTL
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60  # signed-in sessions last 30 days
ADMIN_SESSION_TTL_SECONDS = 12 * 60 * 60  # admin tokens are short-lived — re-enter the password twice a day
JWT_ALGORITHM = "HS256"

# A team member's access code (see generate_access_code below) is a
# different kind of credential from the two above: it isn't short-lived
# or single-use, it's what the member signs in with indefinitely until
# their workspace admin regenerates or disables it — so it needs more
# entropy than a 6-digit OTP or a 4-digit app key.
ACCESS_CODE_LENGTH = 10
_ACCESS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L — easy to read/type when an admin relays it verbally or over chat


class AuthError(ValueError):
    """Raised for any user-facing auth failure — callers turn this into a 400/401."""


def normalize_email(email: str) -> str:
    """Lowercase + strip, and reject anything that isn't roughly email-shaped.
    Raises AuthError with a message safe to show the user."""
    email = (email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if len(email) > 254:
        raise AuthError("Email address is too long.")
    return email


def generate_code() -> str:
    """A cryptographically random 6-digit code, zero-padded (e.g. '004821')."""
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(email: str, code: str, pepper: str) -> str:
    """Hash a code for storage. Binds the hash to the (normalized) email so
    a leaked hash can't be replayed against a different address, and mixes
    in a server-side pepper (env var, never stored in the DB) so the DB
    alone — e.g. a Neon backup — isn't enough to reconstruct valid codes."""
    payload = f"{pepper}:{email}:{code}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_code(email: str, code: str, pepper: str, expected_hash: str) -> bool:
    """Constant-time comparison of a submitted code against a stored hash."""
    if not code or not code.isdigit() or len(code) != CODE_LENGTH:
        return False
    return secrets.compare_digest(hash_code(email, code, pepper), expected_hash)


def generate_access_code() -> str:
    """A team member's ongoing login credential (set by their workspace
    admin — see POST /workspaces/{id}/members in api.py). Restricted to
    an unambiguous alphabet (no 0/O/1/I/L) since an admin typically reads
    this out loud or pastes it into a chat message for the member,
    rather than it being auto-emailed with the code embedded."""
    return "".join(secrets.choice(_ACCESS_CODE_ALPHABET) for _ in range(ACCESS_CODE_LENGTH))


def normalize_access_code(code: str) -> str:
    """Access codes are case-insensitive and tolerate stray spaces or
    dashes a member might introduce copying one out of a chat message."""
    return (code or "").strip().upper().replace(" ", "").replace("-", "")


def verify_access_code(email: str, code: str, pepper: str, expected_hash: str) -> bool:
    """Constant-time comparison against a stored access-code hash. Reuses
    hash_code's email+pepper binding (same reasoning as verify_code) but,
    unlike verify_code, doesn't assume a fixed-length digit-only code —
    an access code is alphanumeric."""
    normalized = normalize_access_code(code)
    if not normalized:
        return False
    return secrets.compare_digest(hash_code(email, normalized, pepper), expected_hash)


def create_session_token(email: str, secret: str) -> str:
    """A signed JWT carrying just the email and issue/expiry times. No
    session table to look up — validity is entirely self-contained in the
    token, which is what keeps auth cheap on a free-tier DB under load."""
    now = int(time.time())
    payload = {"sub": email, "iat": now, "exp": now + SESSION_TTL_SECONDS}
    return _pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_session_token(token: str, secret: str) -> str:
    """Returns the email if the token is valid and unexpired, else raises
    AuthError with a message safe to show the user."""
    if not token:
        raise AuthError("Not signed in.")
    try:
        payload = _pyjwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except _pyjwt.ExpiredSignatureError:
        raise AuthError("Your session expired — sign in again.")
    except _pyjwt.InvalidTokenError:
        raise AuthError("Invalid session — sign in again.")
    email = payload.get("sub")
    if not email or payload.get("role") is not None:
        # The `role` check keeps an admin token (see create_admin_token)
        # from ever being accepted here, even though both are HS256 JWTs
        # signed with the same AUTH_SECRET — the two token kinds must
        # never be interchangeable.
        raise AuthError("Invalid session — sign in again.")
    return email


def extract_bearer_token(authorization_header: Optional[str]) -> str:
    """Pulls the token out of an `Authorization: Bearer <token>` header."""
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise AuthError("Not signed in.")
    return authorization_header[len("Bearer "):].strip()


def create_admin_token(secret: str) -> str:
    """A signed JWT for the admin page — deliberately a distinct token
    shape from a user session (carries role: admin, no email, a much
    shorter TTL) so an admin token can never be confused with, or reused
    as, a signed-in user's session, and vice versa."""
    now = int(time.time())
    payload = {"sub": "admin", "role": "admin", "iat": now, "exp": now + ADMIN_SESSION_TTL_SECONDS}
    return _pyjwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def verify_admin_token(token: str, secret: str) -> None:
    """Raises AuthError (safe to show the user) unless `token` is a valid,
    unexpired admin token. Returns nothing — callers only need to know
    whether it passed."""
    if not token:
        raise AuthError("Not signed in as admin.")
    try:
        payload = _pyjwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except _pyjwt.ExpiredSignatureError:
        raise AuthError("Admin session expired — sign in again.")
    except _pyjwt.InvalidTokenError:
        raise AuthError("Invalid admin session — sign in again.")
    if payload.get("role") != "admin":
        raise AuthError("Invalid admin session — sign in again.")
