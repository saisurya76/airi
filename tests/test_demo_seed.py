import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.auth import hash_code
from airi.demo_seed import seed_demo_data, DEMO_OWNER_EMAIL, DEMO_MEMBER_EMAIL, DEMO_WORKSPACE_TITLE
from airi.workspaces import COE_GATE_KEYS

# seed_demo_data() is orchestration over airi.db — no real database is
# available in this test environment (or in CI), so every db.* call is
# mocked here. analyze()/project()/build_report() are deliberately left
# UN-mocked: they're pure local arithmetic (see demo_seed.py's module
# docstring on why Exact mode is the one tool never seeded), so letting
# them actually run is what catches a real signature mismatch between
# demo_seed.py and the core library, which a fully-mocked test would miss.


def _wire_mock_db(mock_db_module):
    mock_db_module.upsert_demo_user.side_effect = [101, 102]  # owner, then member
    mock_db_module.create_workspace.return_value = {"id": 55, "title": DEMO_WORKSPACE_TITLE}
    counter = {"n": 0}

    def _create_project(*args, **kwargs):
        counter["n"] += 1
        return {"id": 1000 + counter["n"]}

    mock_db_module.create_project.side_effect = _create_project
    mock_db_module.add_workspace_member.return_value = {"id": 1, "email": DEMO_MEMBER_EMAIL}
    return mock_db_module


def test_seed_demo_data_creates_both_demo_accounts():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        seed_demo_data("test-secret")
        assert mock_db_module.upsert_demo_user.call_count == 2
        called_emails = [c.args[0] for c in mock_db_module.upsert_demo_user.call_args_list]
        assert called_emails == [DEMO_OWNER_EMAIL, DEMO_MEMBER_EMAIL]
        print("OK: seed_demo_data_creates_both_demo_accounts")


def test_seed_demo_data_hashes_the_member_access_code_not_plaintext():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        summary = seed_demo_data("test-secret")
        code = summary["member_access_code"]
        assert code and len(code) >= 6
        _workspace_id, _email, _user_id, stored_hash = mock_db_module.add_workspace_member.call_args.args
        assert stored_hash != code  # never the plaintext code itself
        assert stored_hash == hash_code(DEMO_MEMBER_EMAIL, code, "test-secret")
        print("OK: seed_demo_data_hashes_the_member_access_code_not_plaintext")


def test_seed_demo_data_creates_five_projects_of_every_type():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        summary = seed_demo_data("test-secret")
        assert mock_db_module.create_project.call_count == 5
        assert set(summary["project_ids"].keys()) == {
            "support_copilot", "fraud_triage", "analytics_dashboard", "license_request", "sdlc_request",
        }
        project_types = [c.args[4] for c in mock_db_module.create_project.call_args_list]
        assert project_types.count("api_request") == 3
        assert project_types.count("license_request") == 1
        assert project_types.count("sdlc_request") == 1
        print("OK: seed_demo_data_creates_five_projects_of_every_type")


def test_seed_demo_data_seeds_tool_runs_for_api_request_projects_only():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        seed_demo_data("test-secret")
        # support_copilot (4) + fraud_triage (2) + analytics_dashboard (4) = 10.
        # license_request/sdlc_request projects get zero tool runs, matching
        # how those types work for a real user (see workspaces.py's
        # "projects: type" section).
        assert mock_db_module.create_tool_run.call_count == 10
        tools_used = {c.args[1] for c in mock_db_module.create_tool_run.call_args_list}
        assert tools_used == {"analyze", "project", "report"}
        assert "exact" not in tools_used  # deliberately never seeded — see module docstring
        print("OK: seed_demo_data_seeds_tool_runs_for_api_request_projects_only")


def test_seed_demo_data_seeds_coe_governance_with_a_full_ledger_trail():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        seed_demo_data("test-secret")
        # Every one of the 5 demo projects now has CoE governance on —
        # see demo_seed.py's module docstring for the spread across risk
        # tiers this is meant to show.
        assert mock_db_module.set_project_risk.call_count == 5
        assert mock_db_module.set_project_gate_state.call_count == 5
        # Only support_copilot and fraud_triage explicitly assign roles
        # (business_owner/technical_owner/governance_owner each); the
        # other 3 pass roles={} on purpose, to demonstrate the other real
        # setup — every role defaulting to the workspace admin, no setup
        # needed — so set_project_roles is only ever called for those 2.
        assert mock_db_module.set_project_roles.call_count == 2
        event_types = [c.args[2] for c in mock_db_module.create_coe_event.call_args_list]
        assert event_types.count("risk_set") == 5
        assert event_types.count("role_assigned") == 6  # 3 roles x 2 projects with explicit roles
        # All 6 gates touched on every governed project now (see
        # demo_seed.py's gate_plan comments) — 6 x 5, not just the couple
        # that used to be left at the implicit default.
        assert event_types.count("gate_status_changed") == 30
        print("OK: seed_demo_data_seeds_coe_governance_with_a_full_ledger_trail")


def test_seed_demo_data_touches_all_six_gates_on_every_governed_project():
    """Regression guard for 'fill up all gates in demos': every one of
    the 5 demo projects must have every one of the 6 real COE_GATE_KEYS
    represented in its phase_state, not just support_copilot/fraud_triage
    — otherwise 3 of the 5 projects show a mostly-empty Governance tab.
    Also pins that Fraud Triage's Verify gate stays 'flagged' (never
    accidentally 'cleared'), since that's the one load-bearing piece of
    the demo that makes the Mandatory-gate identity-enforcement
    restriction visible without extra setup — see demo_seed.py's
    gate_plan comment."""
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        seed_demo_data("test-secret")
        assert mock_db_module.set_project_gate_state.call_count == 5
        for call in mock_db_module.set_project_gate_state.call_args_list:
            phase_state = call.args[1]
            assert set(phase_state.keys()) == set(COE_GATE_KEYS), (
                f"expected all 6 gates, got {sorted(phase_state.keys())}"
            )
        fraud_triage_phase_state = mock_db_module.set_project_gate_state.call_args_list[1].args[1]
        assert fraud_triage_phase_state["verify"]["status"] == "flagged"
        print("OK: seed_demo_data_touches_all_six_gates_on_every_governed_project")


def test_seed_demo_data_leaves_a_note_on_every_project():
    with patch("airi.demo_seed.db") as mock_db_module:
        _wire_mock_db(mock_db_module)
        seed_demo_data("test-secret")
        assert mock_db_module.create_note.call_count == 5
        print("OK: seed_demo_data_leaves_a_note_on_every_project")


if __name__ == "__main__":
    test_seed_demo_data_creates_both_demo_accounts()
    test_seed_demo_data_hashes_the_member_access_code_not_plaintext()
    test_seed_demo_data_creates_five_projects_of_every_type()
    test_seed_demo_data_seeds_tool_runs_for_api_request_projects_only()
    test_seed_demo_data_seeds_coe_governance_with_a_full_ledger_trail()
    test_seed_demo_data_touches_all_six_gates_on_every_governed_project()
    test_seed_demo_data_leaves_a_note_on_every_project()
    print("\nAll demo_seed sanity checks passed.")
