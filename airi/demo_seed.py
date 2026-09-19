"""
Demo/test data seeding (admin-triggered) — see docs/ADMIN.md's "Demo
data" section and sql/013_admin_demo_users.sql.

seed_demo_data() builds one realistic workspace end to end: five
projects across every project type, saved runs from three of AIRI's
four tools (Standard analyze, Traffic projection, Load-test report —
see the module docstring's note on why Exact mode is deliberately left
out), notes, and CoE governance turned ON for every one of the 5
projects, all 6 gates touched on each, spanning all 3 risk tiers and
both ways roles can be set up (explicit assignment vs. left to default
to the workspace admin):

- support_copilot (Standard) — a healthy lifecycle, mostly cleared.
- fraud_triage (High) — genuinely blocked: Verify stays flagged, so the
  Mandatory-gate identity-enforcement restriction (only the resolved
  accountable role can clear Design/Verify/Release) has something real
  to bump into live, rather than being demonstrated by explanation.
- analytics_dashboard, license_request (Low) — fully cleared, showing
  how lightweight governance is once nothing's actually risky, and that
  it applies just as sensibly to a plain vendor request as to shipping
  an AI feature.
- sdlc_request (Standard) — deliberately left mid-flow (a pilot still
  in progress), rather than resolved either way.

Everything a click through frontend/workspaces.html would show for a
workspace that's actually been used for a while.

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
    default. roles: {role_key: user_id}, or an empty dict to leave every
    role unassigned on purpose — `resolve_coe_roles` defaults each one to
    the workspace admin, so this demonstrates the other real, supported
    setup: a solo/small workspace that never visits the Roles section at
    all, with nothing broken or half-configured about it."""
    tier, explanation = ws.compute_risk_tier(risk_answers)
    db.set_project_risk(project_id, tier, risk_answers, explanation)
    db.create_coe_event(project_id, None, "risk_set", owner_user_id, from_value="", to_value=tier, note="")

    if roles:
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
    """Always builds a fresh baseline workspace — this function has no
    idea whether a demo workspace already exists, and doesn't check;
    that's POST /admin/demo/seed's job (it wipes any existing one first
    — see its own docstring for why "reuse instead of rebuild" was
    tried and dropped). Calling this with a demo workspace still around
    would just create a second one, not merge into it.

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
        # All 6 gates touched, at Standard tier (no Mandatory gate here —
        # see COE_GATES/ENFORCEMENT_LOOKUP — so clearing any of these
        # doesn't require a specific accountable role, just a signed-in
        # active member) — a full, healthy lifecycle, paired below with
        # Fraud Triage's High-tier, still-blocked one for contrast.
        gate_plan={
            "frame": {"status": "cleared", "note": "Approved: reduces average handle time, human always sends the final reply."},
            "design": {"status": "cleared", "note": "Data-retention review with legal closed out — 90-day retention agreed with support ops."},
            "verify": {"status": "cleared", "note": "Reviewed 200 sample transcripts — tone and accuracy both within target, no policy violations found."},
            "release": {"status": "cleared", "note": "Rolled out to 100% of the support queue on Sept 1 — no rollback triggers hit in the first week."},
            "run": {"status": "in_progress", "note": "Live in production — weekly quality spot-checks ongoing, next review scheduled end of month."},
            "evolve_retire": {"status": "not_started", "note": "Nothing to evolve yet — revisit once there's a cost or quality reason to change the model."},
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
        # All 6 gates touched here too, but the story is different on
        # purpose: Design/Verify/Release are Mandatory at High tier (only
        # the resolved accountable role can clear one — see
        # ENFORCEMENT_LOOKUP), and Verify is left FLAGGED rather than
        # cleared specifically so that identity-enforcement restriction
        # has something real to bump into live — Release, Run, and
        # Evolve-or-retire are explicit "not_started" writes (with a
        # note explaining why), not just left blank, so the History tab
        # shows the reasoning rather than silence.
        gate_plan={
            "frame": {"status": "cleared", "note": "Business case approved by Risk & Fraud leadership."},
            "design": {"status": "cleared", "note": "Model, thresholds, and escalation path signed off."},
            "verify": {"status": "flagged", "note": "False-positive rate still 4% at 3x expected peak load — holding until the next model iteration lands, target is under 2%."},
            "release": {"status": "not_started", "note": "Blocked — Release can't start until Verify clears."},
            "run": {"status": "not_started", "note": "Not live yet — stays paused until Release clears."},
            "evolve_retire": {"status": "not_started", "note": "Too early to consider — revisit after the first stable release."},
        },
        roles={"business_owner": owner_id, "technical_owner": member_id, "governance_owner": owner_id},
    )
    db.create_note(p2["id"], "Release gate stays closed until the false-positive rate is under 2% at peak load — see Verify note.")

    p3 = db.create_project(
        workspace_id, "Analytics Dashboard Redesign",
        "Internal analytics dashboard rebuild — natural-language query box on top of the existing warehouse.",
        {"frontend": "React", "backend": "Node", "database": "Snowflake", "ai_services": "OpenAI",
         "ai_model": "GPT-4o", "hosting": "Vercel"},
        "api_request", coe_governance_enabled=True,
    )
    project_ids["analytics_dashboard"] = p3["id"]
    _seed_tool_runs(p3["id"])
    _seed_coe(
        p3["id"], owner_id, member_id,
        risk_answers={"data": "public_internal", "autonomy": "advisory", "exposure": "internal", "reversibility": "easily_reversible"},
        # Low tier — every gate is advisory (see ENFORCEMENT_LOOKUP), so
        # nothing here blocks; the point of this project is to show how
        # lightweight governance is for something genuinely low-stakes.
        # roles={} (below) also demonstrates the OTHER supported setup
        # from support_copilot/fraud_triage's explicit assignment: no
        # role setup at all, defaulting to the workspace admin.
        gate_plan={
            "frame": {"status": "cleared", "note": "Approved — clear internal need, no autonomy or exposure risk."},
            "design": {"status": "cleared", "note": "Standard internal architecture, no new data classification."},
            "verify": {"status": "cleared", "note": "Query accuracy checked against existing reports — results match."},
            "release": {"status": "cleared", "note": "Rolled out to the analytics team."},
            "run": {"status": "in_progress", "note": "Used daily by the analytics team, no issues reported so far."},
            "evolve_retire": {"status": "not_started", "note": "Nothing to revisit yet."},
        },
        roles={},
    )
    db.create_note(p3["id"], "CoE governance is ON here at Low risk tier — every gate is advisory only, so it's a light touch: internal-only tool, no autonomy, low stakes either way.")

    p4 = db.create_project(
        workspace_id, "Vendor License Request — Copilot Seats",
        "Requesting 12 additional GitHub Copilot seats for the platform team for Q1.",
        {}, "license_request", coe_governance_enabled=True,
    )
    project_ids["license_request"] = p4["id"]
    _seed_coe(
        p4["id"], owner_id, member_id,
        risk_answers={"data": "public_internal", "autonomy": "advisory", "exposure": "internal", "reversibility": "easily_reversible"},
        # Same Low tier as analytics_dashboard, but note that CoE applies
        # here too even though this isn't an AI system being built at all
        # — Frame/Verify/etc. still make sense as "is this a real need /
        # did we actually get what we asked for" checkpoints for a plain
        # vendor request, not just for building software.
        gate_plan={
            "frame": {"status": "cleared", "note": "Manager approved the business case for 12 seats."},
            "design": {"status": "cleared", "note": "No build here — just a vendor purchase; marked reviewed."},
            "verify": {"status": "cleared", "note": "Seats provisioned and confirmed working for all 12 users."},
            "release": {"status": "cleared", "note": "Live — the team has full Copilot access."},
            "run": {"status": "cleared", "note": "No issues since rollout; usage tracked by procurement."},
            "evolve_retire": {"status": "not_started", "note": "Revisit at contract renewal."},
        },
        roles={},
    )
    db.create_note(p4["id"], "Manager approval received — routing to procurement for the PO.")

    p5 = db.create_project(
        workspace_id, "Dev Tool Seat Request — Cursor",
        "Trial request for 5 Cursor seats on the backend team, 60-day pilot before a full rollout decision.",
        {}, "sdlc_request", coe_governance_enabled=True,
    )
    project_ids["sdlc_request"] = p5["id"]
    _seed_coe(
        p5["id"], owner_id, member_id,
        risk_answers={"data": "public_internal", "autonomy": "human_in_loop", "exposure": "internal", "reversibility": "easily_reversible"},
        # Standard tier this time (autonomy scores 1 — a human decides
        # whether to roll the pilot out further) — and deliberately left
        # mid-flow rather than resolved, the 5th project's role in the
        # spread: support_copilot is mostly done, fraud_triage is
        # blocked, analytics_dashboard/license_request are fully clear,
        # this one is still actively in progress.
        gate_plan={
            "frame": {"status": "cleared", "note": "Pilot approved for the backend team, 60-day trial."},
            "design": {"status": "cleared", "note": "Standard IDE/tooling install, no new data flows."},
            "verify": {"status": "in_progress", "note": "Pilot underway — collecting adoption and productivity feedback from the 5 pilot users."},
            "release": {"status": "not_started", "note": "Waiting on pilot results before deciding on a full rollout."},
            "run": {"status": "not_started", "note": "Not applicable until Release."},
            "evolve_retire": {"status": "not_started", "note": "Too early — revisit after the pilot retro."},
        },
        roles={},
    )
    db.create_note(p5["id"], "Pilot starts next sprint — revisit adoption numbers in the retro after 60 days.")

    return {
        "workspace_id": workspace_id,
        "workspace_title": DEMO_WORKSPACE_TITLE,
        "owner_email": DEMO_OWNER_EMAIL,
        "member_email": DEMO_MEMBER_EMAIL,
        "member_access_code": member_code,
        "project_ids": project_ids,
    }
