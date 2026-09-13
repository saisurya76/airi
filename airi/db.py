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
from typing import Optional

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

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
