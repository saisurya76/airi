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
from typing import Any, Dict, Optional, Tuple

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

def validate_workspace_fields(data: Dict[str, Any]) -> Tuple[str, str, str, bool]:
    """Returns (title, target, description, coe_governance_enabled), the
    first three trimmed. Raises WorkspaceError on the first problem
    found. coe_governance_enabled is a plain workspace-wide switch (see
    "projects: CoE governance" below) — nothing to validate beyond
    coercing to bool, same full-replace convention as every other
    workspace/admin setting in this codebase (the form always submits
    the whole state, so there's no partial-update logic to get wrong)."""
    title = (data.get("title") or "").strip()
    target = (data.get("target") or "").strip()
    description = (data.get("description") or "").strip()
    coe_governance_enabled = bool(data.get("coe_governance_enabled"))

    if not title:
        raise WorkspaceError("Workspace title is required.")
    if len(title) > TITLE_MAX_CHARS:
        raise WorkspaceError(f"Workspace title is too long (max {TITLE_MAX_CHARS} characters).")
    if len(target) > TARGET_MAX_CHARS:
        raise WorkspaceError(f"Workspace target is too long (max {TARGET_MAX_CHARS} characters).")
    if len(description) > DESCRIPTION_MAX_CHARS:
        raise WorkspaceError(f"Workspace description is too long (max {DESCRIPTION_MAX_CHARS} characters).")
    return title, target, description, coe_governance_enabled


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
# Every project is one of three types. "api_request" is the original
# (and, until this field existed, only) kind — a project whose saved
# runs come from AIRI's four API-traffic tools (Standard, Exact, Traffic
# projection, Load-test report). "license_request" and "sdlc_request"
# mirror the two newer demo wizards (frontend/license-wizard.html,
# frontend/sdlc-wizard.html) — a project of either kind is for
# organizing that request and its notes, not for running the
# API-traffic tools, which don't apply to a per-seat license or a
# dev-tool seat/usage request.
#
# CoE governance (see below) used to be a 4th type, "coe_initiative",
# that linked back to one of these three. It's now a workspace-wide
# switch instead (`coe_governance_enabled` on the workspace) — when on,
# EVERY project in that workspace, of any of these three types, carries
# the risk tier/gates/roles/ledger; there's no separate governance
# project to create or link.

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


# ---------- projects: CoE governance ----------
#
# The risk-tiered model from the "CoE Phases -> AIRI Workspaces Projects"
# plan doc: one risk tier (computed from 4 factors, worst-factor-wins,
# never averaged), 3 accountable roles (not a 7-column enterprise RACI),
# and 6 gates (not 18 checklist steps) — designed to need almost no
# setup for a low-risk initiative and to get genuinely hard to bypass
# for a high-risk one.
#
# It's a workspace-wide switch (`coe_governance_enabled` on the
# workspace, see validate_workspace_fields), not a project type or a
# per-project opt-in: whoever can create a project is already the
# workspace admin, so "does this workspace do CoE governance" is a
# workspace-level decision. With the switch off, a project's
# risk_tier/coe_roles/coe_phase_state just stay at their defaults and
# none of this ever surfaces — the process is exactly what it was
# before this feature existed. api.py's three write endpoints
# (coe-risk, coe-roles, coe-phases) 400 if the switch is off, so the
# data can't be set behind the workspace's back even by a direct API
# call.
#
# Identity enforcement: clearing a Mandatory gate is restricted to
# whoever resolve_coe_roles() names for that gate's accountable_role
# (api.py's set_project_coe_gate checks this, since it's the one place
# that knows the caller's user_id) — Standard/Advisory gates, and every
# non-"cleared" status, stay open to any active member. Every write
# still goes through the ledger regardless, so nothing is silently lost.

RISK_FACTORS = {
    "data": {
        "label": "Data",
        "question": "Does this touch anything beyond public or internal-only information?",
        "options": [
            {"value": "public_internal", "label": "Public or internal-only", "score": 0},
            {"value": "confidential", "label": "Confidential, but not personal or regulated", "score": 1},
            {"value": "regulated", "label": "Personal, financial, health, or otherwise regulated", "score": 2},
        ],
    },
    "autonomy": {
        "label": "Autonomy",
        "question": "Does it just produce output a human reads, does a human approve each action, or does it act on its own?",
        "options": [
            {"value": "advisory", "label": "Advisory — a human reads the output", "score": 0},
            {"value": "human_in_loop", "label": "A human approves each action", "score": 1},
            {"value": "autonomous", "label": "Acts on its own (sends, changes, spends)", "score": 2},
        ],
    },
    "exposure": {
        "label": "Exposure",
        "question": "Who can reach it?",
        "options": [
            {"value": "internal", "label": "Internal users only", "score": 0},
            {"value": "external", "label": "External / customer-facing", "score": 1},
        ],
    },
    "reversibility": {
        "label": "Reversibility",
        "question": "If it's wrong, how bad is that?",
        "options": [
            {"value": "easily_reversible", "label": "Quietly fixable", "score": 0},
            {"value": "hard_to_reverse", "label": "Costly or hard to undo", "score": 2},
        ],
    },
}

RISK_TIERS = {
    "low": {"label": "Low"},
    "standard": {"label": "Standard"},
    "high": {"label": "High"},
}

_SCORE_TO_TIER = {0: "low", 1: "standard", 2: "high"}

ACCOUNTABLE_ROLES = {
    "business_owner": {
        "label": "Business Owner",
        "description": "Accountable for whether this should exist and whether it's delivering value.",
    },
    "technical_owner": {
        "label": "Technical Owner",
        "description": "Accountable for whether it works, safely, at an acceptable quality and cost.",
    },
    "governance_owner": {
        "label": "Governance Owner",
        "description": "Accountable for whether it meets CoE standards — approved models, architecture, data classification, risk controls.",
    },
}

# key, order, label, the question the assistant asks at that gate, and
# which of the 3 roles is accountable for it. "run" is a standing
# domain, not a one-time gate, but is represented the same way (a
# status that can be revisited any time) for simplicity.
COE_GATES = [
    {
        "key": "frame",
        "order": 1,
        "label": "Frame",
        "guide_question": "Is this a real problem, and who owns the outcome?",
        "accountable_role": "business_owner",
    },
    {
        "key": "design",
        "order": 2,
        "label": "Design",
        "guide_question": "Does this meet the model, architecture, and data standards?",
        "accountable_role": "technical_owner",
    },
    {
        "key": "verify",
        "order": 3,
        "label": "Verify",
        "guide_question": "Does it work, safely, at the required bar?",
        "accountable_role": "technical_owner",
    },
    {
        "key": "release",
        "order": 4,
        "label": "Release",
        "guide_question": "Are we allowed to put real users or production traffic on it?",
        "accountable_role": "governance_owner",
    },
    {
        "key": "run",
        "order": 5,
        "label": "Run",
        "guide_question": "Is it still healthy, and still worth what it costs?",
        "accountable_role": "technical_owner",
    },
    {
        "key": "evolve_retire",
        "order": 6,
        "label": "Evolve or retire",
        "guide_question": "Does a change need to re-clear earlier gates, or is it time to shut down?",
        "accountable_role": "governance_owner",
    },
]

COE_GATE_KEYS = tuple(g["key"] for g in COE_GATES)
COE_GATES_BY_KEY = {g["key"]: g for g in COE_GATES}

GATE_STATUSES = ("not_started", "in_progress", "cleared", "flagged")
DEFAULT_GATE_STATUS = "not_started"

# {tier -> {gate_key -> enforcement level}}. Low is always advisory —
# Guide and Ledger still run, nothing blocks. Levels beyond this table
# are read by the frontend/future agent to size the Guide prompt and
# decide whether an override needs a reason; see COE_CONTROLS-style
# reasoning in the plan doc's "Control state machine" section for the
# 3 levels themselves (advisory / required_justification / mandatory).
ENFORCEMENT_LOOKUP = {
    "low": {key: "advisory" for key in COE_GATE_KEYS},
    "standard": {
        "frame": "advisory",
        "design": "required_justification",
        "verify": "advisory",
        "release": "required_justification",
        "run": "advisory",
        "evolve_retire": "advisory",
    },
    "high": {
        "frame": "advisory",
        "design": "mandatory",
        "verify": "mandatory",
        "release": "mandatory",
        "run": "required_justification",
        "evolve_retire": "required_justification",
    },
}

RISK_NOTE_MAX_CHARS = 2000
GATE_NOTE_MAX_CHARS = 2000


def enforcement_level(tier: str, gate_key: str) -> str:
    """Raises WorkspaceError for an unknown tier/gate — callers always
    pass values already validated by compute_risk_tier/gate key
    membership, so this only fires on a real bug."""
    if tier not in ENFORCEMENT_LOOKUP:
        raise WorkspaceError(f"Unknown risk tier: {tier!r}.")
    if gate_key not in COE_GATE_KEYS:
        raise WorkspaceError(f"Unknown gate: {gate_key!r}.")
    return ENFORCEMENT_LOOKUP[tier][gate_key]


def validate_risk_answers(data: Dict[str, Any]) -> Dict[str, str]:
    """Takes {factor_key: option_value} for all 4 RISK_FACTORS keys,
    returns it unchanged (already validated) — a missing key or unknown
    value raises WorkspaceError naming which factor. All 4 are required;
    unlike tech stack there's no "optional" factor here — the tier isn't
    meaningful with a gap in it."""
    result = {}
    for factor_key, factor in RISK_FACTORS.items():
        value = data.get(factor_key)
        if not value:
            raise WorkspaceError(f"'{factor['label']}' is required.")
        known_values = {opt["value"] for opt in factor["options"]}
        if value not in known_values:
            raise WorkspaceError(f"Unknown value for '{factor['label']}': {value!r}.")
        result[factor_key] = value
    return result


def compute_risk_tier(risk_factors: Dict[str, str]) -> Tuple[str, str]:
    """Returns (tier, explanation). Worst-factor-wins: the tier is set by
    the single highest-scoring answer, not an average of the 4 — one
    serious factor is enough to make the whole initiative High, the same
    way a single failed safety check outweighs three passing ones. That
    also makes the tier self-explaining: the explanation always names
    the one factor that caused it, so "High" is never a bare label.
    Assumes risk_factors has already passed validate_risk_answers."""
    best_score = -1
    best_factor_key = None
    best_option_label = None
    for factor_key, value in risk_factors.items():
        factor = RISK_FACTORS[factor_key]
        option = next(opt for opt in factor["options"] if opt["value"] == value)
        if option["score"] > best_score:
            best_score = option["score"]
            best_factor_key = factor_key
            best_option_label = option["label"]
    tier = _SCORE_TO_TIER[max(best_score, 0)]
    factor_label = RISK_FACTORS[best_factor_key]["label"]
    explanation = f"{RISK_TIERS[tier]['label']}, because of {factor_label.lower()}: {best_option_label.lower()}."
    return tier, explanation


def validate_gate_update(gate_key: str, status: str, note: str) -> Tuple[str, str, str]:
    """Returns (gate_key, status, note) trimmed/validated. A 'flagged'
    status always requires a non-empty note — that's the override
    reason, the one piece of the Ledger that's never optional."""
    if gate_key not in COE_GATE_KEYS:
        raise WorkspaceError(f"Unknown gate: {gate_key!r}.")
    if status not in GATE_STATUSES:
        raise WorkspaceError(f"Unknown gate status: {status!r}.")
    note = (note or "").strip()
    if len(note) > GATE_NOTE_MAX_CHARS:
        raise WorkspaceError(f"Note is too long (max {GATE_NOTE_MAX_CHARS} characters).")
    if status == "flagged" and not note:
        raise WorkspaceError("Flagging a gate needs a reason — add a short note.")
    return gate_key, status, note


def validate_coe_roles(data: Dict[str, Any]) -> Dict[str, Optional[int]]:
    """Takes {role_key: user_id or None}, returns it normalized (every
    ACCOUNTABLE_ROLES key present, unknown keys dropped, missing/None
    kept as None — resolved to the workspace admin at read time, not
    here, since this module has no DB access)."""
    result: Dict[str, Optional[int]] = {key: None for key in ACCOUNTABLE_ROLES}
    for role_key in ACCOUNTABLE_ROLES:
        value = data.get(role_key)
        if value is None or value == "":
            continue
        try:
            result[role_key] = int(value)
        except (TypeError, ValueError):
            raise WorkspaceError(f"'{ACCOUNTABLE_ROLES[role_key]['label']}' must be a user id.")
    return result


def resolve_coe_roles(coe_roles: Dict[str, Any], workspace_admin_user_id: int) -> Dict[str, int]:
    """A workspace that just turned CoE governance on needs zero role
    setup: any role nobody has explicitly assigned defaults to the
    workspace admin, so Guide/Ledger/Gate all work immediately without
    an admin having to fill in a form first."""
    return {
        role_key: (coe_roles.get(role_key) or workspace_admin_user_id)
        for role_key in ACCOUNTABLE_ROLES
    }
