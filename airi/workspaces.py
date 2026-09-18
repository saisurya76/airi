"""
Validation and pure logic for the workspaces/projects feature (Phase 1
of the plan in docs/WORKSPACES.md): the 4-digit "app key" PIN, and
field validation for workspaces/members/projects.

Like airi/auth.py, this is pure logic only — no network calls, no DB
access — so it's fully unit-testable without Postgres. Database access
lives in airi/db.py; wiring it all together into endpoints (including
ownership checks, since "does this workspace belong to this user"
needs a DB round trip) lives in api.py. This module is an API-layer
concern: part of the `airi` package for reuse, but never imported by
airi/__init__.py — the core analyze()/project()/build_report() path
has no notion of users, workspaces, or projects.
"""

import hashlib
import re
import secrets
from typing import Any, Dict, Tuple

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_APP_KEY_RE = re.compile(r"^\d{4}$")

TITLE_MAX_CHARS = 200
TARGET_MAX_CHARS = 2000
DESCRIPTION_MAX_CHARS = 4000
TECH_STACK_VALUE_MAX_CHARS = 200


class WorkspaceError(ValueError):
    """Raised for any user-facing validation failure here — callers turn
    this into a 400."""


# ---------- app key (a re-confirmation PIN, not a real credential) ----------
#
# Deliberately only 4 digits and only ever compared server-side against a
# hash — this is *not* meant to be a second authentication factor (the
# session JWT is what actually authenticates every request). It exists
# solely so a destructive action (delete workspace/project) requires
# re-entering something the user chose, the same spirit as typing a
# resource's name into a "type DELETE to confirm" box, except AIRI asks
# for this one PIN everywhere rather than a different phrase per resource.
# Because it's not a security boundary, the hash is unsalted-per-user
# beyond binding to the user id (see hash_app_key) — a fast, simple check
# is the right tradeoff here, same as OTP codes in airi/auth.py.

def normalize_app_key(app_key: str) -> str:
    """Raises WorkspaceError with a message safe to show the user."""
    app_key = (app_key or "").strip()
    if not _APP_KEY_RE.match(app_key):
        raise WorkspaceError("App key must be exactly 4 digits.")
    return app_key


def hash_app_key(user_id: int, app_key: str, pepper: str) -> str:
    """Binds the hash to the user id (so a leaked hash can't be replayed
    against a different account) and mixes in a server-side pepper
    (AUTH_SECRET, never stored in the DB) — same construction as
    airi.auth.hash_code."""
    payload = f"{pepper}:appkey:{user_id}:{app_key}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def verify_app_key(user_id: int, app_key: str, pepper: str, expected_hash: str) -> bool:
    """Constant-time comparison. False (never an exception) for a
    malformed key — a wrong-shaped guess is still just a wrong guess."""
    if not app_key or not _APP_KEY_RE.match(app_key):
        return False
    return secrets.compare_digest(hash_app_key(user_id, app_key, pepper), expected_hash)


# ---------- workspaces ----------

def validate_workspace_fields(data: Dict[str, Any]) -> Tuple[str, str, str]:
    """Returns (title, target, description), all trimmed. Raises
    WorkspaceError on the first problem found."""
    title = (data.get("title") or "").strip()
    target = (data.get("target") or "").strip()
    description = (data.get("description") or "").strip()

    if not title:
        raise WorkspaceError("Workspace title is required.")
    if len(title) > TITLE_MAX_CHARS:
        raise WorkspaceError(f"Workspace title is too long (max {TITLE_MAX_CHARS} characters).")
    if len(target) > TARGET_MAX_CHARS:
        raise WorkspaceError(f"Workspace target is too long (max {TARGET_MAX_CHARS} characters).")
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise WorkspaceError(f"Workspace description is too long (max {DESCRIPTION_MAX_CHARS} characters).")
    return title, target, description


def normalize_member_email(email: str) -> str:
    """Same shape check as sign-in email (airi.auth.normalize_email) —
    duplicated rather than imported so this module has no dependency on
    airi.auth, keeping the two independently testable."""
    email = (email or "").strip().lower()
    if not email or not _EMAIL_RE.match(email):
        raise WorkspaceError("Enter a valid email address for the team member.")
    if len(email) > 254:
        raise WorkspaceError("Email address is too long.")
    return email


# ---------- projects: tech stack ----------
#
# One category per layer of a typical AI-powered app, "ai_services" and
# "ai_model" compulsory (every project AIRI analyzes is, by definition,
# making calls to *some* AI service/model — that's the one thing this
# tool can't leave blank) and everything else optional, since not every
# project has (say) a mobile client or its own cache layer.

TECH_STACK_CATEGORIES = {
    "frontend": {"label": "Frontend", "required": False},
    "backend": {"label": "Backend / middle tier", "required": False},
    "database": {"label": "Database", "required": False},
    "ai_services": {"label": "AI service / provider", "required": True},
    "ai_model": {"label": "AI model", "required": True},
    "hosting": {"label": "Hosting / infrastructure", "required": False},
    "cache_queue": {"label": "Cache / queue", "required": False},
    "mobile": {"label": "Mobile", "required": False},
    "other": {"label": "Other", "required": False},
}

REQUIRED_TECH_STACK_CATEGORIES = tuple(
    key for key, meta in TECH_STACK_CATEGORIES.items() if meta["required"]
)


def validate_tech_stack(data: Dict[str, Any], require_full: bool = True) -> Dict[str, str]:
    """Takes a dict of (a subset of) TECH_STACK_CATEGORIES keys -> free
    text, returns a fully-populated dict (every category present, unset
    optional ones as ""). Unknown keys are silently dropped, same
    convention as airi.author.validate_profile.

    Raises WorkspaceError if any value is too long, and — only when
    require_full is True (the default) — if a required category is
    missing/blank. require_full=False is for a project whose type isn't
    "api_request" (see PROJECT_TYPES): a License/SDLC-tool request project
    doesn't make calls to an AI service/model the way an API-traffic
    project does, so "AI service/provider" and "AI model" don't apply."""
    result = {key: "" for key in TECH_STACK_CATEGORIES}
    for key, meta in TECH_STACK_CATEGORIES.items():
        value = data.get(key)
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise WorkspaceError(f"'{meta['label']}' must be text.")
        value = value.strip()
        if len(value) > TECH_STACK_VALUE_MAX_CHARS:
            raise WorkspaceError(f"'{meta['label']}' is too long (max {TECH_STACK_VALUE_MAX_CHARS} characters).")
        result[key] = value

    if require_full:
        for key in REQUIRED_TECH_STACK_CATEGORIES:
            if not result[key]:
                raise WorkspaceError(f"'{TECH_STACK_CATEGORIES[key]['label']}' is required for every project.")
    return result


# ---------- projects: type ----------
#
# Every project is one of three request types. "api_request" is the
# original (and, until this field existed, only) kind — a project whose
# saved runs come from AIRI's four API-traffic tools (Standard, Exact,
# Traffic projection, Load-test report). "license_request" and
# "sdlc_request" mirror the two newer demo wizards (frontend/
# license-wizard.html, frontend/sdlc-wizard.html) — a project of either
# kind is for organizing that request and its notes, not for running the
# API-traffic tools, which don't apply to a per-seat license or a dev-tool
# seat/usage request.

PROJECT_TYPES = {
    "api_request": {"label": "API request"},
    "license_request": {"label": "License request"},
    "sdlc_request": {"label": "SDLC tool request"},
}

DEFAULT_PROJECT_TYPE = "api_request"


def validate_project_type(value: Any) -> str:
    """Normalizes and validates a project's type. Blank/missing defaults
    to DEFAULT_PROJECT_TYPE — every project AIRI tracked before this
    field existed was, implicitly, an API request project."""
    value = (value or "").strip() or DEFAULT_PROJECT_TYPE
    if value not in PROJECT_TYPES:
        raise WorkspaceError(f"Unknown project type: {value!r}.")
    return value


def validate_project_fields(data: Dict[str, Any]) -> Tuple[str, str, Dict[str, str], str]:
    """Returns (title, description, tech_stack, project_type). Raises
    WorkspaceError on the first problem found."""
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not title:
        raise WorkspaceError("Project title is required.")
    if len(title) > TITLE_MAX_CHARS:
        raise WorkspaceError(f"Project title is too long (max {TITLE_MAX_CHARS} characters).")
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise WorkspaceError(f"Project description is too long (max {DESCRIPTION_MAX_CHARS} characters).")

    project_type = validate_project_type(data.get("project_type"))
    tech_stack = validate_tech_stack(data.get("tech_stack") or {}, require_full=(project_type == "api_request"))
    return title, description, tech_stack, project_type
