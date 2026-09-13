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
    title, desc, tech_stack = ws.validate_project_fields(
        {"title": " My Project ", "description": " d ", "tech_stack": _valid_tech_stack()}
    )
    assert title == "My Project"
    assert desc == "d"
    assert tech_stack["ai_model"] == "claude-3-5-sonnet"
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
    try:
        ws.validate_project_fields({"title": "ok"})
        assert False, "should have raised"
    except ws.WorkspaceError:
        pass
    print("OK: validate_project_fields_defaults_tech_stack_to_empty_dict")


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
    print("\nAll workspaces sanity checks passed.")
