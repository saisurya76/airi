import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import workspaces as ws


# ---------- app key ----------

def test_normalize_app_key_accepts_four_digits():
    assert ws.normalize_app_key("1234") == "1234"
    assert ws.normalize_app_key("0000") == "0000"
    print("OK: normalize_app_key_accepts_four_digits")


def test_normalize_app_key_rejects_bad_shapes():
    for bad in ["", "123", "12345", "12a4", "abcd", "  ", "12 4"]:
        try:
            ws.normalize_app_key(bad)
            assert False, f"should have rejected {bad!r}"
        except ws.WorkspaceError:
            pass
    print("OK: normalize_app_key_rejects_bad_shapes")


def test_hash_app_key_is_deterministic_and_bound_to_user():
    h1 = ws.hash_app_key(1, "1234", "pepper")
    h2 = ws.hash_app_key(1, "1234", "pepper")
    h3 = ws.hash_app_key(2, "1234", "pepper")  # different user, same key
    assert h1 == h2
    assert h1 != h3
    print("OK: hash_app_key_is_deterministic_and_bound_to_user")


def test_verify_app_key_roundtrip():
    secret = "s3cr3t"
    h = ws.hash_app_key(7, "4321", secret)
    assert ws.verify_app_key(7, "4321", secret, h) is True
    assert ws.verify_app_key(7, "0000", secret, h) is False
    assert ws.verify_app_key(8, "4321", secret, h) is False  # wrong user id
    print("OK: verify_app_key_roundtrip")


def test_verify_app_key_rejects_malformed_without_raising():
    h = ws.hash_app_key(1, "1234", "pepper")
    for bad in ["", "12", "abcd", None]:
        assert ws.verify_app_key(1, bad, "pepper", h) is False
    print("OK: verify_app_key_rejects_malformed_without_raising")


# ---------- workspace fields ----------

def test_validate_workspace_fields_trims_and_requires_title():
    title, target, desc = ws.validate_workspace_fields({"title": "  My WS  ", "target": " t ", "description": " d "})
    assert (title, target, desc) == ("My WS", "t", "d")
    print("OK: validate_workspace_fields_trims_and_requires_title")


def test_validate_workspace_fields_rejects_missing_title():
    for data in [{}, {"title": ""}, {"title": "   "}]:
        try:
            ws.validate_workspace_fields(data)
            assert False, "should have raised"
        except ws.WorkspaceError as e:
            assert "title" in str(e).lower()
    print("OK: validate_workspace_fields_rejects_missing_title")


def test_validate_workspace_fields_rejects_oversized_values():
    try:
        ws.validate_workspace_fields({"title": "x" * (ws.TITLE_MAX_CHARS + 1)})
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "too long" in str(e).lower()

    try:
        ws.validate_workspace_fields({"title": "ok", "target": "x" * (ws.TARGET_MAX_CHARS + 1)})
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass

    try:
        ws.validate_workspace_fields({"title": "ok", "description": "x" * (ws.DESCRIPTION_MAX_CHARS + 1)})
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_workspace_fields_rejects_oversized_values")


# ---------- member email ----------

def test_normalize_member_email_lowercases_and_trims():
    assert ws.normalize_member_email("  Alice@Example.com  ") == "alice@example.com"
    print("OK: normalize_member_email_lowercases_and_trims")


def test_normalize_member_email_rejects_invalid():
    for bad in ["", "not-an-email", "no-at-sign.com", "  "]:
        try:
            ws.normalize_member_email(bad)
            assert False, f"should have rejected {bad!r}"
        except ws.WorkspaceError:
            pass
    print("OK: normalize_member_email_rejects_invalid")


# ---------- tech stack ----------

def _valid_tech_stack(**overrides):
    base = {"ai_services": "Anthropic API", "ai_model": "claude-3-5-sonnet"}
    base.update(overrides)
    return base


def test_validate_tech_stack_fills_every_category():
    result = ws.validate_tech_stack(_valid_tech_stack())
    assert set(result.keys()) == set(ws.TECH_STACK_CATEGORIES.keys())
    assert result["ai_services"] == "Anthropic API"
    assert result["frontend"] == ""  # optional, unset
    print("OK: validate_tech_stack_fills_every_category")


def test_validate_tech_stack_requires_ai_services_and_ai_model():
    for missing in ["ai_services", "ai_model"]:
        data = _valid_tech_stack()
        data[missing] = ""
        try:
            ws.validate_tech_stack(data)
            assert False, f"should have required {missing}"
        except ws.WorkspaceError as e:
            assert "required" in str(e).lower()

    # confirm the required set is exactly these two, not more/fewer
    assert set(ws.REQUIRED_TECH_STACK_CATEGORIES) == {"ai_services", "ai_model"}
    print("OK: validate_tech_stack_requires_ai_services_and_ai_model")


def test_validate_tech_stack_drops_unknown_keys():
    result = ws.validate_tech_stack(_valid_tech_stack(made_up_category="whatever"))
    assert "made_up_category" not in result
    print("OK: validate_tech_stack_drops_unknown_keys")


def test_validate_tech_stack_rejects_oversized_value():
    try:
        ws.validate_tech_stack(_valid_tech_stack(frontend="x" * (ws.TECH_STACK_VALUE_MAX_CHARS + 1)))
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "too long" in str(e).lower()
    print("OK: validate_tech_stack_rejects_oversized_value")


def test_validate_tech_stack_rejects_non_string_value():
    try:
        ws.validate_tech_stack(_valid_tech_stack(frontend=123))
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_tech_stack_rejects_non_string_value")


# ---------- project fields ----------

def test_validate_project_fields_happy_path():
    title, desc, tech_stack, project_type = ws.validate_project_fields(
        {"title": " My Project ", "description": " d ", "tech_stack": _valid_tech_stack()}
    )
    assert title == "My Project"
    assert desc == "d"
    assert tech_stack["ai_model"] == "claude-3-5-sonnet"
    assert project_type == "api_request"  # default, since none was given
    print("OK: validate_project_fields_happy_path")


def test_validate_project_fields_requires_title():
    try:
        ws.validate_project_fields({"tech_stack": _valid_tech_stack()})
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "title" in str(e).lower()
    print("OK: validate_project_fields_requires_title")


def test_validate_project_fields_propagates_tech_stack_errors():
    try:
        ws.validate_project_fields({"title": "ok", "tech_stack": {}})
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "required" in str(e).lower()
    print("OK: validate_project_fields_propagates_tech_stack_errors")


def test_validate_project_fields_defaults_tech_stack_to_empty_dict():
    # tech_stack omitted entirely -> same as {} -> still enforces required categories
    # for the default project type (api_request)
    try:
        ws.validate_project_fields({"title": "ok"})
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_project_fields_defaults_tech_stack_to_empty_dict")


# ---------- project type ----------

def test_validate_project_type_defaults_to_api_request():
    assert ws.validate_project_type(None) == "api_request"
    assert ws.validate_project_type("") == "api_request"
    assert ws.validate_project_type("   ") == "api_request"
    print("OK: validate_project_type_defaults_to_api_request")


def test_validate_project_type_accepts_all_known_types():
    for key in ws.PROJECT_TYPES:
        assert ws.validate_project_type(key) == key
    assert set(ws.PROJECT_TYPES.keys()) == {"api_request", "license_request", "sdlc_request"}
    print("OK: validate_project_type_accepts_all_known_types")


def test_validate_project_type_rejects_unknown_value():
    try:
        ws.validate_project_type("something_else")
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "unknown project type" in str(e).lower()
    print("OK: validate_project_type_rejects_unknown_value")


def test_validate_project_fields_license_and_sdlc_types_dont_require_tech_stack():
    for project_type in ("license_request", "sdlc_request"):
        title, desc, tech_stack, returned_type = ws.validate_project_fields(
            {"title": "ok", "project_type": project_type}  # no tech_stack at all
        )
        assert returned_type == project_type
        assert tech_stack["ai_services"] == ""  # not required, and not supplied
        assert tech_stack["ai_model"] == ""
    print("OK: validate_project_fields_license_and_sdlc_types_dont_require_tech_stack")


def test_validate_project_fields_propagates_unknown_project_type():
    try:
        ws.validate_project_fields({"title": "ok", "project_type": "bogus"})
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "unknown project type" in str(e).lower()
    print("OK: validate_project_fields_propagates_unknown_project_type")


# ---------- CoE governance: risk tier ----------

def _risk_answers(**overrides):
    base = {"data": "public_internal", "autonomy": "advisory", "exposure": "internal", "reversibility": "easily_reversible"}
    base.update(overrides)
    return base


def test_validate_risk_answers_requires_all_four_factors():
    for missing in ("data", "autonomy", "exposure", "reversibility"):
        answers = _risk_answers()
        del answers[missing]
        try:
            ws.validate_risk_answers(answers)
            assert False, f"should have required {missing}"
        except ws.WorkspaceError:
            pass
    print("OK: validate_risk_answers_requires_all_four_factors")


def test_validate_risk_answers_rejects_unknown_value():
    try:
        ws.validate_risk_answers(_risk_answers(data="bogus"))
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "data" in str(e).lower()
    print("OK: validate_risk_answers_rejects_unknown_value")


def test_compute_risk_tier_all_lowest_is_low():
    tier, explanation = ws.compute_risk_tier(_risk_answers())
    assert tier == "low"
    assert explanation
    print("OK: compute_risk_tier_all_lowest_is_low")


def test_compute_risk_tier_is_worst_factor_not_average():
    # Three factors at their lowest, one at the highest -> still High,
    # never diluted by the other three being fine.
    tier, explanation = ws.compute_risk_tier(_risk_answers(data="regulated"))
    assert tier == "high"
    assert "data" in explanation.lower()
    print("OK: compute_risk_tier_is_worst_factor_not_average")


def test_compute_risk_tier_mid_severity_is_standard():
    tier, _explanation = ws.compute_risk_tier(_risk_answers(autonomy="human_in_loop"))
    assert tier == "standard"
    print("OK: compute_risk_tier_mid_severity_is_standard")


def test_compute_risk_tier_explanation_names_the_winning_factor():
    _tier, explanation = ws.compute_risk_tier(_risk_answers(exposure="external", reversibility="hard_to_reverse"))
    # reversibility scores 2 (same as the max possible) and is listed
    # after exposure in RISK_FACTORS iteration order in this dict, but
    # since dicts preserve insertion order and reversibility is defined
    # last, either could legitimately "win" a tie — what matters is the
    # explanation names a factor that actually scored the max, not a
    # specific one.
    assert "external" in explanation.lower() or "hard to undo" in explanation.lower()
    print("OK: compute_risk_tier_explanation_names_the_winning_factor")


# ---------- CoE governance: enforcement level ----------

def test_enforcement_level_low_tier_always_advisory():
    for gate_key in ws.COE_GATE_KEYS:
        assert ws.enforcement_level("low", gate_key) == "advisory"
    print("OK: enforcement_level_low_tier_always_advisory")


def test_enforcement_level_high_tier_mandatory_at_design_verify_release():
    for gate_key in ("design", "verify", "release"):
        assert ws.enforcement_level("high", gate_key) == "mandatory"
    print("OK: enforcement_level_high_tier_mandatory_at_design_verify_release")


def test_enforcement_level_rejects_unknown_tier_or_gate():
    try:
        ws.enforcement_level("extreme", "design")
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    try:
        ws.enforcement_level("low", "nonexistent_gate")
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: enforcement_level_rejects_unknown_tier_or_gate")


# ---------- CoE governance: gates ----------

def test_coe_gates_cover_all_three_roles():
    roles_used = {g["accountable_role"] for g in ws.COE_GATES}
    assert roles_used == set(ws.ACCOUNTABLE_ROLES.keys())
    print("OK: coe_gates_cover_all_three_roles")


def test_validate_gate_update_happy_path():
    gate_key, status, note = ws.validate_gate_update("design", "in_progress", "  looking good  ")
    assert (gate_key, status, note) == ("design", "in_progress", "looking good")
    print("OK: validate_gate_update_happy_path")


def test_validate_gate_update_rejects_unknown_gate_or_status():
    try:
        ws.validate_gate_update("not_a_gate", "cleared", "")
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    try:
        ws.validate_gate_update("design", "not_a_status", "")
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_gate_update_rejects_unknown_gate_or_status")


def test_validate_gate_update_flagged_requires_a_note():
    try:
        ws.validate_gate_update("release", "flagged", "")
        assert False, "should have raised"
    except ws.WorkspaceError as e:
        assert "reason" in str(e).lower()
    # a note makes it fine
    gate_key, status, note = ws.validate_gate_update("release", "flagged", "model not on the approved list")
    assert status == "flagged" and note
    print("OK: validate_gate_update_flagged_requires_a_note")


# ---------- CoE governance: roles ----------

def test_validate_coe_roles_fills_every_role_key():
    result = ws.validate_coe_roles({"technical_owner": 42})
    assert set(result.keys()) == set(ws.ACCOUNTABLE_ROLES.keys())
    assert result["technical_owner"] == 42
    assert result["business_owner"] is None
    print("OK: validate_coe_roles_fills_every_role_key")


def test_validate_coe_roles_rejects_non_integer():
    try:
        ws.validate_coe_roles({"business_owner": "not-a-number"})
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_coe_roles_rejects_non_integer")


def test_resolve_coe_roles_defaults_unassigned_to_admin():
    resolved = ws.resolve_coe_roles({"technical_owner": 42}, workspace_admin_user_id=1)
    assert resolved == {"business_owner": 1, "technical_owner": 42, "governance_owner": 1}
    print("OK: resolve_coe_roles_defaults_unassigned_to_admin")


if __name__ == "__main__":
    test_normalize_app_key_accepts_four_digits()
    test_normalize_app_key_rejects_bad_shapes()
    test_hash_app_key_is_deterministic_and_bound_to_user()
    test_verify_app_key_roundtrip()
    test_verify_app_key_rejects_malformed_without_raising()
    test_validate_workspace_fields_trims_and_requires_title()
    test_validate_workspace_fields_rejects_missing_title()
    test_validate_workspace_fields_rejects_oversized_values()
    test_normalize_member_email_lowercases_and_trims()
    test_normalize_member_email_rejects_invalid()
    test_validate_tech_stack_fills_every_category()
    test_validate_tech_stack_requires_ai_services_and_ai_model()
    test_validate_tech_stack_drops_unknown_keys()
    test_validate_tech_stack_rejects_oversized_value()
    test_validate_tech_stack_rejects_non_string_value()
    test_validate_project_fields_happy_path()
    test_validate_project_fields_requires_title()
    test_validate_project_fields_propagates_tech_stack_errors()
    test_validate_project_fields_defaults_tech_stack_to_empty_dict()
    test_validate_project_type_defaults_to_api_request()
    test_validate_project_type_accepts_all_known_types()
    test_validate_project_type_rejects_unknown_value()
    test_validate_project_fields_license_and_sdlc_types_dont_require_tech_stack()
    test_validate_project_fields_propagates_unknown_project_type()
    test_validate_risk_answers_requires_all_four_factors()
    test_validate_risk_answers_rejects_unknown_value()
    test_compute_risk_tier_all_lowest_is_low()
    test_compute_risk_tier_is_worst_factor_not_average()
    test_compute_risk_tier_mid_severity_is_standard()
    test_compute_risk_tier_explanation_names_the_winning_factor()
    test_enforcement_level_low_tier_always_advisory()
    test_enforcement_level_high_tier_mandatory_at_design_verify_release()
    test_enforcement_level_rejects_unknown_tier_or_gate()
    test_coe_gates_cover_all_three_roles()
    test_validate_gate_update_happy_path()
    test_validate_gate_update_rejects_unknown_gate_or_status()
    test_validate_gate_update_flagged_requires_a_note()
    test_validate_coe_roles_fills_every_role_key()
    test_validate_coe_roles_rejects_non_integer()
    test_resolve_coe_roles_defaults_unassigned_to_admin()
    print("\nAll workspaces sanity checks passed.")
