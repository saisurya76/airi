"""
Thin Postgres access for AIRI's "Exact" flavor auth (Neon in production).

Two tables only (see sql/001_auth_schema.sql): `users` and `otp_codes`.
Deliberately not an ORM — this is five small parameterized queries, and
a dependency like SQLAlchemy would be overkill for it.

Lazy pool init: importing this module (or importing api.py, which imports
it) never touches the database or requires DATABASE_URL to be set. The
pool is only created the first time a function here is actually called —
so the existing /analyze, /project, /report* endpoints keep working with
zero DB configuration, exactly as before. Only the new /auth/* and
/analyze/exact endpoints need DATABASE_URL.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.pool
from psycopg2.extras import Json, RealDictCursor

_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None


class DatabaseNotConfigured(RuntimeError):
    """Raised when a DB-backed feature is used but DATABASE_URL isn't set —
    turned into a clear 503 by the API layer rather than a raw connection
    traceback."""


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise DatabaseNotConfigured(
                "DATABASE_URL is not set — the 'Exact' flavor's sign-in isn't configured on this deployment."
            )
        # Small pool: a free-tier Render instance is one small dyno talking
        # to Neon's own pooled connection string, so there's no benefit to
        # holding many connections open.
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=5, dsn=dsn)
    return _pool


@contextmanager
def _cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        conn.autocommit = True
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        pool.putconn(conn)


def reset_pool_for_tests() -> None:
    """Test-only: drop the cached pool so a changed DATABASE_URL takes effect."""
    global _pool
    if _pool is not None:
        _pool.closeall()
    _pool = None


# ---------- otp_codes ----------

def count_recent_otp_requests(email: str, since: datetime, purpose: str = "login") -> int:
    """How many codes of this purpose has this email requested since
    `since`? Used for rate limiting (both the 60-second cooldown and the
    daily cap).

    Filtered by purpose (see otp_codes.purpose, sql/011) so a login code
    and a CoE-toggle step-up code draw from SEPARATE per-email budgets —
    this used to be a single shared budget across both purposes, which
    meant a workspace admin who'd used up (or merely recently requested)
    a sign-in code could get a 429 trying to toggle CoE governance
    minutes later, and someone toggling governance for a second project
    right after the first could get 429'd by their own immediately-prior
    toggle-code request. Each purpose now gets its own independent
    60-second cooldown and 5/day cap."""
    with _cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM otp_codes WHERE lower(email) = lower(%s) AND purpose = %s AND created_at >= %s",
            (email, purpose, since),
        )
        return cur.fetchone()["n"]


def create_otp_code(email: str, code_hash: str, expires_at: datetime, purpose: str = "login") -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO otp_codes (email, code_hash, expires_at, purpose) VALUES (%s, %s, %s, %s) RETURNING id",
            (email, code_hash, expires_at, purpose),
        )
        return cur.fetchone()["id"]


def get_latest_unconsumed_code(email: str, purpose: str = "login") -> Optional[dict]:
    """`purpose` must match the value create_otp_code was called with for
    the code being looked for — see airi.auth.LOGIN_OTP_PURPOSE /
    COE_TOGGLE_OTP_PURPOSE. Without this filter, requesting a step-up
    code shortly after (or before) a login code for the same email could
    make verification pick up the wrong one."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT id, code_hash, expires_at, attempts, consumed
            FROM otp_codes
            WHERE lower(email) = lower(%s) AND purpose = %s AND consumed = FALSE
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, purpose),
        )
        return cur.fetchone()


def increment_attempts(code_id: int) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = %s", (code_id,))


def consume_code(code_id: int) -> None:
    with _cursor() as cur:
        cur.execute("UPDATE otp_codes SET consumed = TRUE WHERE id = %s", (code_id,))


def delete_old_codes(older_than: timedelta = timedelta(days=7)) -> int:
    """Housekeeping for Neon's 0.5 GB free-tier cap — call occasionally
    (e.g. from a cron hitting a maintenance endpoint), not on every request."""
    cutoff = datetime.now(timezone.utc) - older_than
    with _cursor() as cur:
        cur.execute("DELETE FROM otp_codes WHERE created_at < %s", (cutoff,))
        return cur.rowcount


# ---------- app_config ----------

def get_config(key: str) -> Optional[str]:
    """Read one admin-configurable setting (see sql/002_app_config.sql).
    Returns None if no row exists — callers treat that as "no override,
    fall back to an env var / default", never as an error."""
    with _cursor() as cur:
        cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
        row = cur.fetchone()
        return row["value"] if row else None


def set_config(key: str, value: str) -> None:
    """Write (or overwrite) one admin-configurable setting."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_config (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (key, value),
        )


# ---------- users ----------

def upsert_user_login(email: str) -> int:
    """Creates the user row on first login, updates last_login_at on every
    login after that. Returns the user id (not currently used for anything
    beyond existing — sessions are JWTs, not a DB-backed session table).

    Always stores the lowercased email: `users.email` has a plain unique
    constraint (for ON CONFLICT to target) plus a case-insensitive index
    as a backstop, so this function is the single place that has to keep
    them in agreement — callers may pass any casing."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (email, last_login_at)
            VALUES (lower(%s), now())
            ON CONFLICT (email) DO UPDATE SET last_login_at = now()
            RETURNING id
            """,
            (email,),
        )
        return cur.fetchone()["id"]


def get_user_id_by_email(email: str) -> Optional[int]:
    """Resolves a signed-in session's email (the only thing a session JWT
    carries — see airi/auth.py) to a `users.id`, for every workspaces/
    projects call below. None only if the email has genuinely never
    logged in, which shouldn't happen for a valid session token."""
    with _cursor() as cur:
        cur.execute("SELECT id FROM users WHERE lower(email) = lower(%s)", (email,))
        row = cur.fetchone()
        return row["id"] if row else None


def get_terms_accepted_at(user_id: int):
    """None means this user hasn't accepted the current Terms & Conditions
    yet (see sql/007_terms_acceptance.sql) — the frontend gates entry into
    the signed-in app behind this, on both the OTP and member-access-code
    login paths, since it's a per-account fact independent of which one
    produced the current session."""
    with _cursor() as cur:
        cur.execute("SELECT terms_accepted_at FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return row["terms_accepted_at"] if row else None


def accept_terms(user_id: int) -> None:
    """Records this user's acceptance of the current Terms & Conditions,
    timestamped — an audit trail of consent, not just a client-side flag."""
    with _cursor() as cur:
        cur.execute("UPDATE users SET terms_accepted_at = now() WHERE id = %s", (user_id,))


# ---------- user_profile (workspaces feature: the "app key" PIN) ----------

def get_user_profile(user_id: int) -> Optional[dict]:
    """None means the row doesn't exist yet (user has never saved an app
    key) — distinct from a row existing with app_key_hash = NULL, which
    this schema never actually produces (see set_app_key_hash)."""
    with _cursor() as cur:
        cur.execute("SELECT user_id, app_key_hash, updated_at FROM user_profile WHERE user_id = %s", (user_id,))
        return cur.fetchone()


def set_app_key_hash(user_id: int, app_key_hash: str) -> None:
    """Creates the profile row on first save, overwrites the hash on any
    later change — always a full replace, there's only one app key."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO user_profile (user_id, app_key_hash, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (user_id) DO UPDATE SET app_key_hash = EXCLUDED.app_key_hash, updated_at = now()
            """,
            (user_id, app_key_hash),
        )


# ---------- workspaces ----------

_WORKSPACE_FIELDS = "id, owner_user_id, title, target, description, coe_governance_enabled, created_at, updated_at"


def create_workspace(owner_user_id: int, title: str, target: str, description: str, coe_governance_enabled: bool = False) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO workspaces (owner_user_id, title, target, description, coe_governance_enabled)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {_WORKSPACE_FIELDS}
            """,
            (owner_user_id, title, target, description, coe_governance_enabled),
        )
        return cur.fetchone()


def list_workspaces(user_id: int) -> List[dict]:
    """Every workspace this user can reach: the ones they own (role
    "admin") plus the ones where they're an active team member (role
    "member") — see sql/006_workspace_member_access.sql. Includes a
    member/project count per workspace so the workspace-list view
    doesn't need N follow-up queries."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT w.*, 'admin' AS role,
                   (SELECT count(*) FROM workspace_members m WHERE m.workspace_id = w.id) AS member_count,
                   (SELECT count(*) FROM projects p WHERE p.workspace_id = w.id) AS project_count
            FROM workspaces w
            WHERE w.owner_user_id = %(user_id)s

            UNION ALL

            SELECT w.*, 'member' AS role,
                   (SELECT count(*) FROM workspace_members m2 WHERE m2.workspace_id = w.id) AS member_count,
                   (SELECT count(*) FROM projects p WHERE p.workspace_id = w.id) AS project_count
            FROM workspaces w
            JOIN workspace_members wm ON wm.workspace_id = w.id
            WHERE wm.user_id = %(user_id)s AND wm.status = 'active'

            ORDER BY created_at DESC
            """,
            {"user_id": user_id},
        )
        return cur.fetchall()


def get_workspace(workspace_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"SELECT {_WORKSPACE_FIELDS} FROM workspaces WHERE id = %s", (workspace_id,))
        return cur.fetchone()


def update_workspace(workspace_id: int, title: str, target: str, description: str) -> Optional[dict]:
    """Basic-details full replace. Deliberately does NOT touch
    coe_governance_enabled any more (sql/011) — that switch now requires
    a step-up email code (see set_workspace_coe_governance below and PUT
    /workspaces/{id}/coe-governance in api.py), so it can't ride along
    on an ordinary title/target/description save."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE workspaces SET title = %s, target = %s, description = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_WORKSPACE_FIELDS}
            """,
            (title, target, description, workspace_id),
        )
        return cur.fetchone()


def set_workspace_coe_governance(workspace_id: int, enabled: bool) -> Optional[dict]:
    """The step-up-gated toggle (see PUT /workspaces/{id}/coe-governance
    in api.py, which verifies a one-time code before calling this) —
    changes only what new projects in this workspace default to; see
    set_project_coe_governance for the switch that actually governs an
    existing project."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE workspaces SET coe_governance_enabled = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_WORKSPACE_FIELDS}
            """,
            (enabled, workspace_id),
        )
        return cur.fetchone()


def delete_workspace(workspace_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
        return cur.rowcount > 0


# ---------- workspace_members ----------
#
# Phase 2 (sql/006_workspace_member_access.sql): a member row is now a
# real second login identity, not just a notify-list entry — user_id is
# resolved (via upsert_user_login) the moment the admin adds them,
# status is 'active'/'disabled' (the admin's toggle, without removing
# the row), and access_code_hash is the hash of their persistent login
# credential (see airi/auth.py's generate_access_code/verify_access_code
# and POST /auth/member-login in api.py). The hash is deliberately left
# out of _MEMBER_FIELDS — only the two functions below that actually
# need it (login verification, and overwriting it on regenerate) touch
# that column at all.

_MEMBER_FIELDS = "id, workspace_id, email, user_id, status, added_at"


def add_workspace_member(workspace_id: int, email: str, user_id: int, access_code_hash: str) -> Optional[dict]:
    """Returns the new member row (never including the code hash), or
    None if that email is already a member of this workspace (caller
    turns that into a 409, not a 500)."""
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO workspace_members (workspace_id, email, user_id, status, access_code_hash)
            VALUES (%s, lower(%s), %s, 'active', %s)
            ON CONFLICT (workspace_id, lower(email)) DO NOTHING
            RETURNING {_MEMBER_FIELDS}
            """,
            (workspace_id, email, user_id, access_code_hash),
        )
        return cur.fetchone()


def list_workspace_members(workspace_id: int) -> List[dict]:
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_MEMBER_FIELDS} FROM workspace_members WHERE workspace_id = %s ORDER BY added_at ASC",
            (workspace_id,),
        )
        return cur.fetchall()


def get_workspace_member(workspace_id: int, member_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_MEMBER_FIELDS} FROM workspace_members WHERE workspace_id = %s AND id = %s",
            (workspace_id, member_id),
        )
        return cur.fetchone()


def remove_workspace_member(workspace_id: int, member_id: int) -> Optional[dict]:
    """Returns the deleted row (so the caller has the email to notify),
    or None if no such member existed."""
    with _cursor() as cur:
        cur.execute(
            f"DELETE FROM workspace_members WHERE workspace_id = %s AND id = %s RETURNING {_MEMBER_FIELDS}",
            (workspace_id, member_id),
        )
        return cur.fetchone()


def set_workspace_member_status(workspace_id: int, member_id: int, status: str) -> Optional[dict]:
    """The admin's disable/enable toggle. A real relationship stays on
    the row (unlike remove_workspace_member's hard delete), so
    re-enabling doesn't require re-adding the member or issuing a new
    code."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE workspace_members SET status = %s
            WHERE workspace_id = %s AND id = %s
            RETURNING {_MEMBER_FIELDS}
            """,
            (status, workspace_id, member_id),
        )
        return cur.fetchone()


def regenerate_workspace_member_code(workspace_id: int, member_id: int, access_code_hash: str) -> Optional[dict]:
    """Overwrites a member's access-code hash — used when the admin
    regenerates a lost/compromised code. Since only the hash is ever
    stored, there's no way to recover the old code; this replaces it
    outright."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE workspace_members SET access_code_hash = %s
            WHERE workspace_id = %s AND id = %s
            RETURNING {_MEMBER_FIELDS}
            """,
            (access_code_hash, workspace_id, member_id),
        )
        return cur.fetchone()


def get_membership(workspace_id: int, user_id: int) -> Optional[dict]:
    """The requesting user's own membership row in this workspace, if
    any — used by the permission-check helpers in api.py to decide
    whether a non-owner caller has *any* relationship to the workspace
    (None -> 404) and, if so, whether it's currently active (status !=
    'active' -> 403)."""
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_MEMBER_FIELDS} FROM workspace_members WHERE workspace_id = %s AND user_id = %s",
            (workspace_id, user_id),
        )
        return cur.fetchone()


def get_active_memberships_by_email(email: str) -> List[dict]:
    """Every active membership for this email, *including* the access
    code hash — used only by the member-login flow (POST
    /auth/member-login in api.py) to check a submitted code against each
    workspace this email was added to. A member added to several
    workspaces has a separate code per workspace, so login checks all of
    them."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT id, workspace_id, email, user_id, status, access_code_hash, added_at
            FROM workspace_members
            WHERE lower(email) = lower(%s) AND status = 'active' AND access_code_hash IS NOT NULL
            """,
            (email,),
        )
        return cur.fetchall()


# ---------- projects ----------

_PROJECT_FIELDS = (
    "id, workspace_id, title, description, tech_stack, project_type, "
    "risk_tier, risk_factors, risk_explanation, coe_roles, coe_phase_state, "
    "coe_governance_enabled, coe_gate_ai_guides, created_at, updated_at"
)


def create_project(
    workspace_id: int, title: str, description: str, tech_stack: Dict[str, Any], project_type: str,
    coe_governance_enabled: bool,
) -> dict:
    """coe_governance_enabled has no default on purpose — the caller
    (POST /workspaces/{id}/projects in api.py) must always resolve it
    explicitly from the parent workspace's current switch, so a new
    project's starting governance state is never silently forgotten."""
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO projects (workspace_id, title, description, tech_stack, project_type, coe_governance_enabled)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {_PROJECT_FIELDS}
            """,
            (workspace_id, title, description, Json(tech_stack), project_type, coe_governance_enabled),
        )
        return cur.fetchone()


def list_projects(workspace_id: int) -> List[dict]:
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_PROJECT_FIELDS} FROM projects WHERE workspace_id = %s ORDER BY created_at ASC",
            (workspace_id,),
        )
        return cur.fetchall()


def get_project(project_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"SELECT {_PROJECT_FIELDS} FROM projects WHERE id = %s", (project_id,))
        return cur.fetchone()


def update_project(project_id: int, title: str, description: str, tech_stack: Dict[str, Any], project_type: str) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET title = %s, description = %s, tech_stack = %s, project_type = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (title, description, Json(tech_stack), project_type, project_id),
        )
        return cur.fetchone()


def delete_project(project_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        return cur.rowcount > 0


# ---------- project_tool_runs (workspaces feature, Phase 2) ----------

_TOOL_RUN_FIELDS = "id, project_id, tool, label, input, result, created_at"


def create_tool_run(project_id: int, tool: str, label: str, input_data: Dict[str, Any], result_data: Dict[str, Any]) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO project_tool_runs (project_id, tool, label, input, result)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING {_TOOL_RUN_FIELDS}
            """,
            (project_id, tool, label, Json(input_data), Json(result_data)),
        )
        return cur.fetchone()


def list_tool_runs(project_id: int, tool: str) -> List[dict]:
    """Most recent first — that's the order a run-history list wants."""
    with _cursor() as cur:
        cur.execute(
            f"""
            SELECT {_TOOL_RUN_FIELDS} FROM project_tool_runs
            WHERE project_id = %s AND tool = %s
            ORDER BY created_at DESC
            """,
            (project_id, tool),
        )
        return cur.fetchall()


def get_tool_run(run_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"SELECT {_TOOL_RUN_FIELDS} FROM project_tool_runs WHERE id = %s", (run_id,))
        return cur.fetchone()


def delete_tool_run(run_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM project_tool_runs WHERE id = %s", (run_id,))
        return cur.rowcount > 0


# ---------- project_notes (workspaces feature, Phase 3) ----------

_NOTE_FIELDS = "id, project_id, body, created_at"


def create_note(project_id: int, body: str) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO project_notes (project_id, body)
            VALUES (%s, %s)
            RETURNING {_NOTE_FIELDS}
            """,
            (project_id, body),
        )
        return cur.fetchone()


def list_notes(project_id: int) -> List[dict]:
    """Most recent first — matches the spec's "readonly list, most recent
    comment first" framing."""
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_NOTE_FIELDS} FROM project_notes WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        )
        return cur.fetchall()


def get_note(note_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"SELECT {_NOTE_FIELDS} FROM project_notes WHERE id = %s", (note_id,))
        return cur.fetchone()


def delete_note(note_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM project_notes WHERE id = %s", (note_id,))
        return cur.rowcount > 0


# ---------- CoE governance (workspaces feature, Phase 6a/6b/6c) ----------
#
# risk_tier/risk_factors/risk_explanation/coe_roles/coe_phase_state on
# `projects` are current-state caches (see sql/008_coe_governance.sql) —
# each setter here does a full replace of its one field, same convention
# as update_project. coe_control_events below is the append-only ledger
# those overwrites would otherwise lose history from.
#
# coe_governance_enabled itself (sql/010, sql/011) lives on both
# `workspaces` and `projects` now — set_workspace_coe_governance only
# controls what a *new* project defaults to; set_project_coe_governance
# is the one that actually governs an existing project day to day. Both
# setters are called only after the step-up email code has verified
# (api.py), never straight from a general settings save.
#
# coe_gate_ai_guides (sql/012) is the one field here that ISN'T a plain
# full-replace — see set_project_gate_ai_guide's own docstring for why it
# does an atomic JSONB merge instead.

def set_project_risk(project_id: int, risk_tier: str, risk_factors: Dict[str, str], risk_explanation: str) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET risk_tier = %s, risk_factors = %s, risk_explanation = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (risk_tier, Json(risk_factors), risk_explanation, project_id),
        )
        return cur.fetchone()


def set_project_roles(project_id: int, coe_roles: Dict[str, Any]) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET coe_roles = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (Json(coe_roles), project_id),
        )
        return cur.fetchone()


def set_project_gate_state(project_id: int, coe_phase_state: Dict[str, Any]) -> Optional[dict]:
    """Takes the FULL new coe_phase_state dict (the caller merges the one
    changed gate into what it already read) — same full-replace shape as
    tech_stack, so there's no partial-JSONB-update logic to get wrong."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET coe_phase_state = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (Json(coe_phase_state), project_id),
        )
        return cur.fetchone()


def set_project_coe_governance(project_id: int, enabled: bool) -> Optional[dict]:
    """The step-up-gated per-project switch (see PUT
    /projects/{id}/coe-governance in api.py, which verifies a one-time
    code before calling this). This is the one that actually governs the
    project day to day — set_workspace_coe_governance only changes what
    a *new* project in that workspace starts out as."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET coe_governance_enabled = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (enabled, project_id),
        )
        return cur.fetchone()


def set_project_gate_ai_guide(project_id: int, gate_key: str, checklist: List[str], provider: str) -> Optional[dict]:
    """Live AI Guide per gate (Phase 6c; see sql/012 and
    airi/ai_guide_provider.py). Deliberately an ATOMIC JSONB merge
    (`coalesce(...) || %s::jsonb`), not a read-modify-write like every
    other CoE setter in this section: two admins regenerating different
    gates' guides for the same project at close to the same time is a
    real scenario this feature introduces — a read-modify-write could
    have each caller's UPDATE start from the same pre-write JSON and
    write back a full replace missing the other's key, silently dropping
    one of the two guides. The `||` merge instead only ever touches the
    one gate_key being written, whatever else is already in the column.
    coalesce(...) guards a NULL value even though sql/012's DEFAULT is
    already '{}'::jsonb — defense in depth, not a fix for anything
    actually reachable today."""
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects
            SET coe_gate_ai_guides = coalesce(coe_gate_ai_guides, '{{}}'::jsonb) || %s::jsonb,
                updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (
                Json({gate_key: {"items": checklist, "provider": provider, "generated_at": datetime.now(timezone.utc).isoformat()}}),
                project_id,
            ),
        )
        return cur.fetchone()


_COE_EVENT_FIELDS = "id, project_id, gate_key, event_type, actor_user_id, from_value, to_value, note, created_at"


def create_coe_event(
    project_id: int,
    gate_key: Optional[str],
    event_type: str,
    actor_user_id: Optional[int],
    from_value: str,
    to_value: str,
    note: str,
) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO coe_control_events (project_id, gate_key, event_type, actor_user_id, from_value, to_value, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING {_COE_EVENT_FIELDS}
            """,
            (project_id, gate_key, event_type, actor_user_id, from_value, to_value, note),
        )
        return cur.fetchone()


def list_coe_events(project_id: int) -> List[dict]:
    """Most recent first — same convention as list_notes."""
    with _cursor() as cur:
        cur.execute(
            f"SELECT {_COE_EVENT_FIELDS} FROM coe_control_events WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,),
        )
        return cur.fetchall()
