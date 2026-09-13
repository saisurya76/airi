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

def count_recent_otp_requests(email: str, since: datetime) -> int:
    """How many codes has this email requested since `since`? Used for
    rate limiting (both the 60-second cooldown and the daily cap)."""
    with _cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM otp_codes WHERE lower(email) = lower(%s) AND created_at >= %s",
            (email, since),
        )
        return cur.fetchone()["n"]


def create_otp_code(email: str, code_hash: str, expires_at: datetime) -> int:
    with _cursor() as cur:
        cur.execute(
            "INSERT INTO otp_codes (email, code_hash, expires_at) VALUES (%s, %s, %s) RETURNING id",
            (email, code_hash, expires_at),
        )
        return cur.fetchone()["id"]


def get_latest_unconsumed_code(email: str) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            """
            SELECT id, code_hash, expires_at, attempts, consumed
            FROM otp_codes
            WHERE lower(email) = lower(%s) AND consumed = FALSE
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email,),
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

_WORKSPACE_FIELDS = "id, owner_user_id, title, target, description, created_at, updated_at"


def create_workspace(owner_user_id: int, title: str, target: str, description: str) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO workspaces (owner_user_id, title, target, description)
            VALUES (%s, %s, %s, %s)
            RETURNING {_WORKSPACE_FIELDS}
            """,
            (owner_user_id, title, target, description),
        )
        return cur.fetchone()


def list_workspaces(owner_user_id: int) -> List[dict]:
    """Includes a member/project count per workspace so the workspace-list
    view doesn't need N follow-up queries."""
    with _cursor() as cur:
        cur.execute(
            f"""
            SELECT w.*,
                   (SELECT count(*) FROM workspace_members m WHERE m.workspace_id = w.id) AS member_count,
                   (SELECT count(*) FROM projects p WHERE p.workspace_id = w.id) AS project_count
            FROM workspaces w
            WHERE w.owner_user_id = %s
            ORDER BY w.created_at DESC
            """,
            (owner_user_id,),
        )
        return cur.fetchall()


def get_workspace(workspace_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(f"SELECT {_WORKSPACE_FIELDS} FROM workspaces WHERE id = %s", (workspace_id,))
        return cur.fetchone()


def update_workspace(workspace_id: int, title: str, target: str, description: str) -> Optional[dict]:
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


def delete_workspace(workspace_id: int) -> bool:
    with _cursor() as cur:
        cur.execute("DELETE FROM workspaces WHERE id = %s", (workspace_id,))
        return cur.rowcount > 0


# ---------- workspace_members ----------

def add_workspace_member(workspace_id: int, email: str) -> Optional[dict]:
    """Returns the new member row, or None if that email is already a
    member of this workspace (caller turns that into a 409, not a 500)."""
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO workspace_members (workspace_id, email)
            VALUES (%s, lower(%s))
            ON CONFLICT (workspace_id, lower(email)) DO NOTHING
            RETURNING id, workspace_id, email, added_at
            """,
            (workspace_id, email),
        )
        return cur.fetchone()


def list_workspace_members(workspace_id: int) -> List[dict]:
    with _cursor() as cur:
        cur.execute(
            "SELECT id, workspace_id, email, added_at FROM workspace_members WHERE workspace_id = %s ORDER BY added_at ASC",
            (workspace_id,),
        )
        return cur.fetchall()


def get_workspace_member(workspace_id: int, member_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            "SELECT id, workspace_id, email, added_at FROM workspace_members WHERE workspace_id = %s AND id = %s",
            (workspace_id, member_id),
        )
        return cur.fetchone()


def remove_workspace_member(workspace_id: int, member_id: int) -> Optional[dict]:
    """Returns the deleted row (so the caller has the email to notify),
    or None if no such member existed."""
    with _cursor() as cur:
        cur.execute(
            "DELETE FROM workspace_members WHERE workspace_id = %s AND id = %s RETURNING id, workspace_id, email, added_at",
            (workspace_id, member_id),
        )
        return cur.fetchone()


# ---------- projects ----------

_PROJECT_FIELDS = "id, workspace_id, title, description, tech_stack, created_at, updated_at"


def create_project(workspace_id: int, title: str, description: str, tech_stack: Dict[str, Any]) -> dict:
    with _cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO projects (workspace_id, title, description, tech_stack)
            VALUES (%s, %s, %s, %s)
            RETURNING {_PROJECT_FIELDS}
            """,
            (workspace_id, title, description, Json(tech_stack)),
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


def update_project(project_id: int, title: str, description: str, tech_stack: Dict[str, Any]) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute(
            f"""
            UPDATE projects SET title = %s, description = %s, tech_stack = %s, updated_at = now()
            WHERE id = %s
            RETURNING {_PROJECT_FIELDS}
            """,
            (title, description, Json(tech_stack), project_id),
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
