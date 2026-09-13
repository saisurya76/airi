import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import tool_runs


def test_tool_name_enum_has_exactly_the_four_tools():
    assert set(t.value for t in tool_runs.ToolName) == {"analyze", "exact", "project", "report"}
    print("OK: tool_name_enum_has_exactly_the_four_tools")


def test_validate_label_trims_and_allows_blank():
    assert tool_runs.validate_label("  Baseline check  ") == "Baseline check"
    assert tool_runs.validate_label("") == ""
    assert tool_runs.validate_label(None) == ""
    print("OK: validate_label_trims_and_allows_blank")


def test_validate_label_rejects_oversized():
    try:
        tool_runs.validate_label("x" * (tool_runs.LABEL_MAX_CHARS + 1))
        assert False, "should have raised"
    except tool_runs.ToolRunError as e:
        assert "too long" in str(e).lower()
    print("OK: validate_label_rejects_oversized")


def test_validate_label_accepts_max_length():
    label = "x" * tool_runs.LABEL_MAX_CHARS
    assert tool_runs.validate_label(label) == label
    print("OK: validate_label_accepts_max_length")


if __name__ == "__main__":
    test_tool_name_enum_has_exactly_the_four_tools()
    test_validate_label_trims_and_allows_blank()
    test_validate_label_rejects_oversized()
    test_validate_label_accepts_max_length()
    print("\nAll tool_runs sanity checks passed.")
