"""
Route-table sanity checks against the REAL FastAPI app (fastapi.testclient),
not a network mock.

Every other test in this repo either exercises pure logic (airi/*.py,
no HTTP at all) or drives the frontend against a hand-written mock server
that matches request paths with its own regexes (see the Playwright
scripts under scratchpad/) — never the real api.py route table. That gap
let a real bug ship: `GET /projects/coe-catalog` was declared AFTER
`GET /projects/{project_id}`, so FastAPI/Starlette (which matches routes
in declaration order) tried the `{project_id}` route first, attempted to
parse the literal segment "coe-catalog" as an int, and 422'd — a mock
server matching "/projects/coe-catalog" by regex never reproduces that,
because it doesn't route the way Starlette actually does.

These tests hit the unauthenticated, DB-free catalog endpoints (the only
ones that can run with no DATABASE_URL/AUTH_SECRET configured) straight
through fastapi.testclient.TestClient, so they resolve through the exact
same route table a real request does. Add any future fixed-path
"/projects/<literal>" or "/workspaces/<literal>" endpoint here too.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

import api

client = TestClient(api.app)


def test_coe_catalog_route_is_not_shadowed_by_project_id():
    """Regression test for the exact bug reported against the live
    deployment: this must resolve to coe_catalog() (200, real catalog
    keys), never fall through to get_project() and 422 trying to parse
    "coe-catalog" as project_id: int."""
    resp = client.get("/projects/coe-catalog")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    for key in ("risk_factors", "risk_tiers", "gates", "roles", "gate_statuses", "enforcement_lookup"):
        assert key in body, f"coe-catalog response missing {key!r}: {body}"
    assert "data" in body["risk_factors"]  # a real RISK_FACTORS key, not an empty stand-in
    print("OK: coe_catalog_route_is_not_shadowed_by_project_id")


def test_tech_stack_categories_route_is_not_shadowed_by_project_id():
    resp = client.get("/projects/tech-stack-categories")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert "ai_services" in resp.json()
    print("OK: tech_stack_categories_route_is_not_shadowed_by_project_id")


def test_project_types_route_is_not_shadowed_by_project_id():
    resp = client.get("/projects/project-types")
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert "api_request" in resp.json()
    print("OK: project_types_route_is_not_shadowed_by_project_id")


def test_project_id_route_still_reports_not_found_for_a_real_int_path():
    """The flip side: /projects/{project_id} must still work for actual
    numeric ids (404 here since there's no DB/no such project — proves
    the route matched and ran, rather than erroring before it got there)."""
    resp = client.get("/projects/999999")
    assert resp.status_code in (401, 404, 503), f"unexpected status for a real project id: {resp.status_code}: {resp.text}"
    print("OK: project_id_route_still_reports_not_found_for_a_real_int_path")


def test_ai_guide_route_is_registered_and_shaped_correctly():
    """Regression guard for the Live AI Guide endpoint (Phase 6c): checks
    it's actually wired into the route table at the expected path shape
    (project_id, then a literal /coe-phases/, then gate_key, then a
    literal /ai-guide — extra segments after {project_id}, so this can
    never suffer the coe-catalog-style shadowing bug, but a typo in the
    path string itself wouldn't show up any other way)."""
    matches = [r for r in api.app.routes if getattr(r, "path", None) == "/projects/{project_id}/coe-phases/{gate_key}/ai-guide"]
    assert matches, "POST /projects/{project_id}/coe-phases/{gate_key}/ai-guide is not registered"
    assert "POST" in matches[0].methods
    print("OK: ai_guide_route_is_registered_and_shaped_correctly")


def test_ai_guide_rate_limit_is_its_own_separate_budget():
    """Regression guard for the coe_toggle 429 lesson (see
    docs/WORKSPACES.md's "The two switches"): AI Guide must never share
    its rate-limit counter with Exact mode's — a burst of one shouldn't
    eat into the other's budget."""
    assert api._ai_guide_call_log is not api._exact_call_log
    assert isinstance(api.AI_GUIDE_CALLS_PER_MINUTE, int) and api.AI_GUIDE_CALLS_PER_MINUTE > 0
    print("OK: ai_guide_rate_limit_is_its_own_separate_budget")


def test_admin_demo_and_users_routes_are_registered_and_password_gated():
    """Regression guard for the admin demo-data feature: every new
    /admin/* route must actually be wired into the route table (a typo
    in the decorator path is invisible to a plain grep), and every one
    must refuse an unauthenticated caller before touching the database —
    same _require_admin gate as the rest of /admin/*, checked before any
    DB access, so this must never reach a DatabaseNotConfigured 503
    ahead of the auth check."""
    routes_by_path = {getattr(r, "path", None): r for r in api.app.routes}
    expected = [
        ("GET", "/admin/users"),
        ("POST", "/admin/demo/seed"),
        ("POST", "/admin/demo/login"),
        ("POST", "/admin/demo/member-code"),
        ("POST", "/admin/demo/disable"),
        ("POST", "/admin/demo/enable"),
        ("DELETE", "/admin/demo/data"),
        ("DELETE", "/admin/demo/user"),
    ]
    for method, path in expected:
        assert path in routes_by_path, f"{method} {path} is not registered"
        assert method in routes_by_path[path].methods, f"{method} {path} is registered but not for {method}"

    # 401 (bad/missing token) or 503 (this test env has no AUTH_SECRET
    # configured at all) — either way, never a 200 and never a database
    # error, proving _require_admin runs and rejects before any DB call.
    resp = client.get("/admin/users")
    assert resp.status_code in (401, 503), f"expected 401 or 503 with no admin token, got {resp.status_code}: {resp.text}"
    resp = client.post("/admin/demo/seed")
    assert resp.status_code in (401, 503), f"expected 401 or 503 with no admin token, got {resp.status_code}: {resp.text}"
    resp = client.post("/admin/demo/member-code")
    assert resp.status_code in (401, 503), f"expected 401 or 503 with no admin token, got {resp.status_code}: {resp.text}"
    print("OK: admin_demo_and_users_routes_are_registered_and_password_gated")


def test_demo_seed_always_wipes_before_rebuilding():
    """Regression guard for the staleness bug reported against the live
    deployment: a second "Seed demo data" click used to silently reuse
    whatever workspace the demo owner already had, so it never reflected
    later changes to airi/demo_seed.py's baseline (e.g. more CoE gates
    filled in) until an admin remembered to separately wipe first. Pins
    that admin_seed_demo_data always calls delete_demo_workspaces
    (idempotent — a no-op if there's nothing yet) before seed_demo_data,
    so "Seed demo data" alone is always enough to get the current
    baseline, no separate wipe step required."""
    import inspect
    seed_src = inspect.getsource(api.admin_seed_demo_data)
    # "seed_demo_data(" alone would also match this function's own
    # "def admin_seed_demo_data(" signature line, so pin the actual call
    # (with its argument) instead.
    assert "db.delete_demo_workspaces()" in seed_src
    assert "seed_demo_data(secret)" in seed_src
    assert seed_src.index("db.delete_demo_workspaces()") < seed_src.index("seed_demo_data(secret)")
    print("OK: demo_seed_always_wipes_before_rebuilding")


def test_demo_disable_flips_both_demo_login_paths_not_just_one():
    """Regression guard: disabling the demo identity has to block BOTH
    the owner's OTP-free login (users.disabled) and the member's
    access-code login (workspace_members.status) — see
    db.set_demo_users_disabled / db.set_demo_memberships_status. A fix
    that only touched one of the two would leave one demo login path
    reachable after "disable" — this pins that api.py's disable/enable
    endpoints always call both."""
    import inspect
    disable_src = inspect.getsource(api.admin_disable_demo)
    enable_src = inspect.getsource(api.admin_enable_demo)
    for src in (disable_src, enable_src):
        assert "set_demo_users_disabled" in src
        assert "set_demo_memberships_status" in src
    print("OK: demo_disable_flips_both_demo_login_paths_not_just_one")


def test_disabled_user_is_rejected_even_with_a_valid_session_token():
    """Regression guard for the central disabled-account check added to
    _require_session_email_and_user_id (sql/013's users.disabled): a
    technically-valid, unexpired session JWT must still be refused with
    403 once the account is disabled — proving the check happens on
    every request against the current DB row, not just at token-issue
    time. GET /workspaces is used here specifically because it routes
    through _require_user_id -> _require_session_email_and_user_id (the
    central helper) — unlike GET /auth/me, which resolves the user id a
    different way and is a known, accepted gap (see api.py)."""
    from unittest.mock import patch
    from airi.auth import create_session_token

    email = "disabled-user@example.com"
    with patch.dict(os.environ, {"AUTH_SECRET": "test-secret-for-disabled-check"}):
        token = create_session_token(email, "test-secret-for-disabled-check")
        with patch("api.db") as mock_db_module:
            mock_db_module.get_user_by_email.return_value = {"id": 42, "disabled": True, "is_demo": True}
            resp = client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 403, f"expected 403 for a disabled account, got {resp.status_code}: {resp.text}"

            # Sanity check on the other side: an otherwise-identical, non-disabled
            # user must NOT be rejected by this same check (proves the 403 above
            # is actually gated on `disabled`, not on the mock itself, the token,
            # or some other unrelated failure).
            mock_db_module.get_user_by_email.return_value = {"id": 42, "disabled": False, "is_demo": True}
            mock_db_module.list_workspaces.return_value = []
            resp = client.get("/workspaces", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200, f"expected 200 for an enabled account, got {resp.status_code}: {resp.text}"
    print("OK: disabled_user_is_rejected_even_with_a_valid_session_token")


def test_coe_toggle_rate_limits_are_more_generous_than_login():
    """Regression guard for the 429 an admin hit doing completely normal
    interactive use of the CoE toggle (flip a project on, cancel, retry,
    flip it back off...): POST /auth/coe-governance/request-code used to
    share /auth/request-code's strict 60s/5-per-day budget, which exists
    to stop the ANONYMOUS, arbitrary-target login endpoint from spamming
    a third party. The toggle endpoint only ever emails the already-
    signed-in caller's own address, so it isn't that same abuse vector
    and gets its own, deliberately looser limits — this pins that they
    stay looser rather than silently drifting back to the login ones."""
    assert api.COE_TOGGLE_REQUEST_COOLDOWN_SECONDS < api.OTP_REQUEST_COOLDOWN_SECONDS
    assert api.COE_TOGGLE_DAILY_REQUEST_LIMIT > api.OTP_DAILY_REQUEST_LIMIT
    print("OK: coe_toggle_rate_limits_are_more_generous_than_login")


if __name__ == "__main__":
    test_coe_catalog_route_is_not_shadowed_by_project_id()
    test_tech_stack_categories_route_is_not_shadowed_by_project_id()
    test_project_types_route_is_not_shadowed_by_project_id()
    test_project_id_route_still_reports_not_found_for_a_real_int_path()
    test_ai_guide_route_is_registered_and_shaped_correctly()
    test_ai_guide_rate_limit_is_its_own_separate_budget()
    test_admin_demo_and_users_routes_are_registered_and_password_gated()
    test_demo_seed_always_wipes_before_rebuilding()
    test_demo_disable_flips_both_demo_login_paths_not_just_one()
    test_disabled_user_is_rejected_even_with_a_valid_session_token()
    test_coe_toggle_rate_limits_are_more_generous_than_login()
    print("\nAll api route sanity checks passed.")
