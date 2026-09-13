"""
Runtime-configurable settings that can be flipped without a redeploy —
currently just one: whether Exact mode uses AIRI's shared server-held
provider keys ("test mode") or requires each signed-in user to bring
their own key ("BYOK mode").

Precedence, highest wins:
  1. An admin override stored in Postgres (app_config table) — set via
     the admin page, POST /admin/config. Survives restarts.
  2. The AIRI_TEST_MODE environment variable — the deployment default,
     set once in Render/whatever host and left alone.
  3. A hardcoded default (test mode ON) if neither is set — so a fresh
     deployment with no config at all is usable out of the box in demo
     mode rather than failing closed for every user.

Like airi/auth.py and airi/exact_provider.py, this is an API-layer
concern: part of the `airi` package for reuse, but never imported by
airi/__init__.py — the core analyze()/project()/build_report() path
has no notion of "test mode" at all.

If the database isn't configured (db.DatabaseNotConfigured), that's
treated exactly like "no admin override exists" — the env var / default
still apply. A DB hiccup here should never take down /analyze/exact;
it should just mean the admin override isn't available right now.
"""

import os
from typing import Tuple

from airi import db

TEST_MODE_KEY = "test_mode"
DEFAULT_TEST_MODE = True  # a fresh deployment with zero config ships in demo/test mode


def _env_test_mode() -> Tuple[bool, bool]:
    """Returns (value, was_set) for the AIRI_TEST_MODE env var."""
    raw = os.environ.get("AIRI_TEST_MODE")
    if raw is None:
        return DEFAULT_TEST_MODE, False
    return raw.strip().lower() in ("1", "true", "yes", "on"), True


def get_test_mode() -> Tuple[bool, str]:
    """Returns (value, source) where source is one of:
      "admin"   — an override is stored in app_config
      "env"     — AIRI_TEST_MODE env var is set, no admin override
      "default" — neither is set, using the hardcoded default
    """
    try:
        stored = db.get_config(TEST_MODE_KEY)
    except db.DatabaseNotConfigured:
        stored = None

    if stored is not None:
        return stored.strip().lower() in ("1", "true", "yes", "on"), "admin"

    env_value, env_was_set = _env_test_mode()
    return env_value, ("env" if env_was_set else "default")


def set_test_mode(value: bool) -> None:
    """Writes an admin override to app_config — takes precedence over the
    env var until cleared (there's deliberately no "clear override" API
    yet; re-running with the env var's own value has the same effect for
    the toggle, just leaves a DB row behind)."""
    db.set_config(TEST_MODE_KEY, "true" if value else "false")
