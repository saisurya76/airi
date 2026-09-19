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


if __name__ == "__main__":
    test_coe_catalog_route_is_not_shadowed_by_project_id()
    test_tech_stack_categories_route_is_not_shadowed_by_project_id()
    test_project_types_route_is_not_shadowed_by_project_id()
    test_project_id_route_still_reports_not_found_for_a_real_int_path()
    print("\nAll api route sanity checks passed.")
