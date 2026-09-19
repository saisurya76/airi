"""
Demo/test data seeding (admin-triggered) — see docs/ADMIN.md's "Demo
data" section and sql/013_admin_demo_users.sql.

seed_demo_data() builds one realistic workspace end to end: five
projects across every project type, saved runs from three of AIRI's
four tools (Standard analyze, Traffic projection, Load-test report —
see the module docstring's note on why Exact mode is deliberately left
out), notes, and a full CoE governance example at both a Standard and a
High risk tier — the High one walks through a Mandatory gate, including
the one it's still too early to clear, so that enforcement behavior is
visible without having to hand-build it. Everything a click through
frontend/workspaces.html would show for a workspace that's actually
been used for a while.

Exact mode is deliberately NOT seeded here, unlike the other three
tools: its whole point is a real, provider-verified count, and every
other seeded run in this module is honestly reproducible local
arithmetic (analyze()/project()/build_report() never call out to
anything). Faking an "Exact" result would put a number on screen that
looks provider-verified but isn't — the admin can generate a real one
in seconds with their own BYOK key once they're browsing as the demo
user, which is a better demo of that feature than a canned fake anyway.

Two accounts are created, both flagged is_demo = true (see
airi/db.py's upsert_demo_user) and given an email address that's
deliberately NOT real/receivable — logging in as either happens through
the admin panel (POST /admin/demo/login mints the owner a session
directly; the member's real, ordinary access-code login — POST
/auth/member-login — works for the member as-is, no bypass needed,
since that path was never email-based to begin with).

Pure orchestration, like a very large single api.py endpoint handler
would be — this lives in its own module only so that handler stays a
few lines. Every DB write funnels through the same db.*/ws.* functions
a real user's actions already go through (create_project,
set_project_risk, create_coe_event, ...), so a demo project is
indistinguishable, in the data it produces, from one a real user built
by hand through the UI.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from . import db
from . import workspaces as ws
from .analyzer import analyze
from .auth import generate_access_code, hash_code
from .projector import Archetype
from .projector import project as run_projection
from .report import build_report

DEMO_OWNER_EMAIL = "demo-owner@airi-demo.local"
DEMO_MEMBER_EMAIL = "demo-member@airi-demo.local"

DEMO_WORKSPACE_TITLE = "AIRI Demo Workspace"


def _analyze_dict(prompt: str, model: str, expected_output_tokens: int = 200) -> Dict[str, Any]:
    return analyze(prompt=prompt, model=model, expected_output_tokens=expected_output_tokens).to_dict()


def _seed_tool_runs(project_id: int) -> None:
    """Standard analyze + Traffic projection + Load-test report runs for
    an api_request project with CoE governance either on or off — the
    three CoE gates/tool types this function is called for don't differ
    by governance state, only by which project_id they're attached to."""
    a1 = _analyze_dict(
        "You are a support copilot. Read this customer message and draft a helpful, empathetic reply, "
        "citing our refund policy if relevant.\n\nCustomer: My subscription renewed but I was charged twice this month.",
        "claude-3-5-sonnet", 250,
    )
    db.create_tool_run(project_id, "analyze", "Baseline reply draft", {
        "model": "claude-3-5-sonnet", "expected_output_tokens": 250,
    }, a1)

    a2 = _analyze_dict(
        "Summarize this 40-message support thread into a 3-bullet handoff note for the on-call agent.",
        "claude-3-5-haiku", 120,
    )
    db.create_tool_run(project_id, "analyze", "Thread summarization", {
        "model": "claude-3-5-haiku", "expected_output_tokens": 120,
    }, a2)

    archetypes = [
        Archetype(name="Ticket summarization", volume=5000, prompt="Summarize this support ticket in 2 sentences.",
                   model="claude-3-5-haiku", expected_output_tokens=120),
        Archetype(name="Reply drafting", volume=2000, prompt="Draft a customer reply for this ticket.",
                   model="claude-3-5-sonnet", expected_output_tokens=250),
    ]
    proj_result = run_projection(archetypes).to_dict()
    db.create_tool_run(project_id, "project", "Monthly volume projection", {
        "archetypes": [{"name": a.name, "volume": a.volume, "model": a.model} for a in archetypes],
    }, proj_result)

    records = []
    for i, (model, out_tok) in enumerate([
        ("claude-3-5-sonnet", 240), ("claude-3-5-sonnet", 260), ("claude-3-5-haiku", 110),
        ("claude-3-5-sonnet", 500), ("claude-3-5-haiku", 130),
    ]):
        r = analyze(prompt="Sample load-test request body #%d" % (i + 1), model=model, expected_output_tokens=out_tok).to_dict()
        r["label"] = "support-copilot-reply"
        r["phase"] = "peak" if i == 3 else "normal"
        records.append(r)
    report_result = build_report("Pre-launch load test", records).to_dict()
    db.create_tool_run(project_id, "report", "Pre-launch load test", {"run_name": "Pre-launch load test"}, report_result)


def _seed_coe(project_id: int, owner_user_id: int, member_user_id: int, risk_answers: Dict[str, str], gate_plan: Dict[str, Dict[str, str]], roles: Dict[str, int]) -> None:
    """Walks a project through the risk form, role assignment, and gate
    updates via the exact same db.*/ws.* calls PUT /projects/{id}/coe-risk
    etc. make — including the coe_control_events ledger rows each of
    those endpoints appends, so the seeded History tab reads like a real
    sequence of decisions instead of a single final-state dump.

    gate_plan: {gate_key: {"status": ..., "note": ...}} for every gate
    that should move off "not_started" — gates left out stay at their
    default. roles: {role_key: user_id}."""
    tier, explanation = ws.compute_risk_tier(risk_answers)
    db.set_project_risk(project_id, tier, risk_answers, explanation)
    db.create_coe_event(project_id, None, "risk_set", owner_user_id, from_value="", to_value=tier, note="")

    db.set_project_roles(project_id, roles)
    for role_key, user_id in roles.items():
        db.create_coe_event(project_id, None, "role_assigned", owner_user_id, from_value="", to_value=str(user_id), note=role_key)

    phase_state: Dict[str, Any] = {}
    for gate_key, plan in gate_plan.items():
        gate_key, status, note = ws.validate_gate_update(gate_key, plan["status"], plan.get("note", ""))
        phase_state[gate_key] = {"status": status, "note": note, "updated_at": datetime.now(timezone.utc).isoformat()}
        db.create_coe_event(project_id, gate_key, "gate_status_changed", owner_user_id, from_value="not_started", to_value=status, note=note)
    if phase_state:
        # One full-replace write at the end, same shape as the real
        # endpoint's per-gate PUT — the intermediate per-gate ledger
        # events above are what makes the History tab show each step.
        db.set_project_gate_state(project_id, phase_state)


def seed_demo_data(secret: str) -> Dict[str, Any]:
    """Idempotent in spirit, not by construction: api.py's endpoint only
    calls this when the demo owner doesn't already own a workspace (see
    POST /admin/demo/seed's own docstring) — this function itself always
    builds a fresh baseline, so it's the caller's job not to double-call
    it into a workspace someone (the admin) has since built on top of.

    `secret` is AUTH_SECRET, needed only to hash the demo member's
    access code the exact same way POST /workspaces/{id}/members does
    (see airi.auth.hash_code) — the one place this module touches
    anything auth-secret-shaped, kept as a parameter rather than an env
    read so this module still has zero env-var reads of its own.

    Returns a summary dict — notably `member_access_code`, the demo
    member's plaintext access code, returned exactly once (same
    never-store convention as every other access code in this app;
    see POST /workspaces/{id}/members)."""
    owner_id = db.upsert_demo_user(DEMO_OWNER_EMAIL)
    member_id = db.upsert_demo_user(DEMO_MEMBER_EMAIL)

    workspace = db.create_workspace(
        owner_id,
        DEMO_WORKSPACE_TITLE,
        "See AIRI's full workflow end to end — workspaces, projects of every type, saved tool runs, "
        "notes, team access, and CoE governance at both a Standard and a High risk tier.",
        "Seeded automatically by the admin dashboard's \"Seed demo data\" action. Safe to add to, "
        "rename, or delete like any other workspace — nothing here is protected from normal editing, "
        "only from being silently duplicated by clicking \"Seed demo data\" again.",
        coe_governance_enabled=True,
    )
    workspace_id = workspace["id"]

    member_code = generate_access_code()
    db.add_workspace_member(workspace_id, DEMO_MEMBER_EMAIL, member_id, hash_code(DEMO_MEMBER_EMAIL, member_code, secret))

    project_ids: Dict[str, int] = {}

    p1 = db.create_project(
        workspace_id, "Customer Support Copilot",
        "Drafts reply suggestions and thread summaries for the support team inside the helpdesk tool.",
        {"frontend": "React", "backend": "FastAPI", "database": "Postgres", "ai_services": "Anthropic",
         "ai_model": "Claude 3.5 Sonnet / Haiku", "hosting": "Render"},
        "api_request", coe_governance_enabled=True,
    )
    project_ids["support_copilot"] = p1["id"]
    _seed_tool_runs(p1["id"])
    _seed_coe(
        p1["id"], owner_id, member_id,
        risk_answers={"data": "confidential", "autonomy": "human_in_loop", "exposure": "external", "reversibility": "easily_reversible"},
        gate_plan={
            "frame": {"status": "cleared", "note": "Approved: reduces average handle time, human always sends the final reply."},
            "design": {"status": "in_progress", "note": "Data-retention review with legal still open."},
        },
        roles={"business_owner": owner_id, "technical_owner": member_id, "governance_owner": owner_id},
    )
    db.create_note(p1["id"], "Kickoff note: scoping this to reply drafting and thread summarization only for v1 — no auto-send.")

    p2 = db.create_project(
        workspace_id, "Fraud Triage Agent",
        "Scores incoming transactions in real time and auto-holds the highest-risk ones pending review.",
        {"backend": "Go", "database": "Postgres", "ai_services": "Google", "ai_model": "Gemini 1.5 Pro",
         "hosting": "GCP", "cache_queue": "Redis"},
        "api_request", coe_governance_enabled=True,
    )
    project_ids["fraud_triage"] = p2["id"]
    a = _analyze_dict(
        "Score this transaction for fraud risk on a 0-100 scale and explain the top 3 contributing signals.",
        "gemini-1.5-pro", 300,
    )
    db.create_tool_run(p2["id"], "analyze", "Single-transaction scoring", {"model": "gemini-1.5-pro", "expected_output_tokens": 300}, a)
    archetypes = [Archetype(name="Real-time transaction scoring", volume=500000, prompt="Score this transaction for fraud risk.",
                             model="gemini-1.5-pro", expected_output_tokens=200)]
    proj_result = run_projection(archetypes).to_dict()
    db.create_tool_run(p2["id"], "project", "Projected monthly volume", {"archetypes": [{"name": "Real-time transaction scoring", "volume": 500000}]}, proj_result)
    _seed_coe(
        p2["id"], owner_id, member_id,
        risk_answers={"data": "regulated", "autonomy": "autonomous", "exposure": "external", "reversibility": "hard_to_reverse"},
        gate_plan={
            "frame": {"status": "cleared", "note": "Business case approved by Risk & Fraud leadership."},
            "design": {"status": "cleared", "note": "Model, thresholds, and escalation path signed off."},
            "verify": {"status": "flagged", "note": "False-positive rate still 4% at 3x expected peak load — holding until the next model iteration lands, target is under 2%."},
        },
        roles={"business_owner": owner_id, "technical_owner": member_id, "governance_owner": owner_id},
    )
    db.create_note(p2["id"], "Release gate stays closed until the false-positive rate is under 2% at peak load — see Verify note.")

    p3 = db.create_project(
        workspace_id, "Analytics Dashboard Redesign",
        "Internal analytics dashboard rebuild — natural-language query box on top of the existing warehouse.",
        {"frontend": "React", "backend": "Node", "database": "Snowflake", "ai_services": "OpenAI",
         "ai_model": "GPT-4o", "hosting": "Vercel"},
        "api_request", coe_governance_enabled=False,
    )
    project_ids["analytics_dashboard"] = p3["id"]
    _seed_tool_runs(p3["id"])
    db.create_note(p3["id"], "CoE governance intentionally left off — internal-only tool, no autonomy, low stakes either way.")

    p4 = db.create_project(
        workspace_id, "Vendor License Request — Copilot Seats",
        "Requesting 12 additional GitHub Copilot seats for the platform team for Q1.",
        {}, "license_request", coe_governance_enabled=False,
    )
    project_ids["license_request"] = p4["id"]
    db.create_note(p4["id"], "Manager approval received — routing to procurement for the PO.")

    p5 = db.create_project(
        workspace_id, "Dev Tool Seat Request — Cursor",
        "Trial request for 5 Cursor seats on the backend team, 60-day pilot before a full rollout decision.",
        {}, "sdlc_request", coe_governance_enabled=False,
    )
    project_ids["sdlc_request"] = p5["id"]
    db.create_note(p5["id"], "Pilot starts next sprint — revisit adoption numbers in the retro after 60 days.")

    return {
        "workspace_id": workspace_id,
        "workspace_title": DEMO_WORKSPACE_TITLE,
        "owner_email": DEMO_OWNER_EMAIL,
        "member_email": DEMO_MEMBER_EMAIL,
        "member_access_code": member_code,
        "project_ids": project_ids,
    }
