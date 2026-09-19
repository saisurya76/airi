import sys
import os
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.ai_guide_provider import (
    GateGuideContext,
    AiGuideUnavailable,
    generate_gate_checklist_anthropic,
    generate_gate_checklist_google,
    generate_gate_checklist,
    has_ai_guide_provider,
    MAX_CHECKLIST_ITEMS,
    _parse_checklist,
)


def _context(**overrides):
    defaults = dict(
        project_title="Support ticket triager",
        project_description="Classifies inbound tickets and suggests a reply.",
        project_type="api_request",
        tech_stack={"language": "Python", "ai_services": "Anthropic"},
        risk_tier="standard",
        risk_explanation="Confidential data, human-in-the-loop, internal only.",
        gate_label="Design",
        guide_question="Does this meet the model, architecture, and data standards?",
        accountable_role_label="Technical Owner",
        enforcement_level="advisory",
    )
    defaults.update(overrides)
    return GateGuideContext(**defaults)


def test_has_ai_guide_provider():
    assert has_ai_guide_provider("anthropic") is True
    assert has_ai_guide_provider("google") is True
    assert has_ai_guide_provider("openai") is False
    assert has_ai_guide_provider("unknown") is False
    print("OK: has_ai_guide_provider")


def test_anthropic_returns_parsed_checklist():
    items = ["Confirm PII fields are redacted before logging.", "Pin the model version used for classification."]
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"content": [{"type": "text", "text": json.dumps(items)}]})
        result = generate_gate_checklist_anthropic(_context(), "sk-test")
        assert result == items
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.anthropic.com/v1/messages"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        assert kwargs["headers"]["X-Api-Key"] == "sk-test"
        assert "system" in kwargs["json"]
        assert kwargs["json"]["messages"][0]["role"] == "user"
        print("OK: anthropic_returns_parsed_checklist")


def test_anthropic_error_response_raises_without_echoing_body():
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=401, text="invalid x-api-key -- do not leak me")
        try:
            generate_gate_checklist_anthropic(_context(), "bad-key")
            assert False, "should have raised"
        except AiGuideUnavailable as e:
            assert "401" in str(e)
            assert "do not leak me" not in str(e)
            print("OK: anthropic_error_response_raises_without_echoing_body ->", e)


def test_google_returns_parsed_checklist():
    items = ["Confirm the Gemini system instruction excludes customer PII.", "Load-test the endpoint before external rollout."]
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps(items)}]}}]},
        )
        result = generate_gate_checklist_google(_context(), "AIza-test")
        assert result == items
        args, kwargs = mock_post.call_args
        assert "models/gemini-2.0-flash:generateContent" in args[0]
        assert "key=AIza-test" in args[0]
        assert kwargs["json"]["systemInstruction"]["parts"][0]["text"]
        print("OK: google_returns_parsed_checklist")


def test_google_error_response_raises_without_echoing_body():
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=500, text="internal secret trace -- do not leak me")
        try:
            generate_gate_checklist_google(_context(), "AIza-bad")
            assert False, "should have raised"
        except AiGuideUnavailable as e:
            assert "500" in str(e)
            assert "do not leak me" not in str(e)
            print("OK: google_error_response_raises_without_echoing_body ->", e)


def test_parse_checklist_tolerates_prose_and_code_fence():
    text = "Sure, here you go:\n```json\n[\"Check A\", \"Check B\", \"Check C\"]\n```\nLet me know if you need more!"
    result = _parse_checklist(text)
    assert result == ["Check A", "Check B", "Check C"]
    print("OK: parse_checklist_tolerates_prose_and_code_fence")


def test_parse_checklist_filters_blanks_and_caps_length():
    items = [f"Item {i}" for i in range(20)] + ["", "   "]
    result = _parse_checklist(json.dumps(items))
    assert len(result) == MAX_CHECKLIST_ITEMS
    assert result[0] == "Item 0"
    print("OK: parse_checklist_filters_blanks_and_caps_length")


def test_parse_checklist_raises_on_unparseable_text():
    try:
        _parse_checklist("Sorry, I can't help with that.")
        assert False, "should have raised"
    except AiGuideUnavailable as e:
        print("OK: parse_checklist_raises_on_unparseable_text ->", e)


def test_generate_gate_checklist_dispatches_by_provider():
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"content": [{"type": "text", "text": json.dumps(["A"])}]})
        assert generate_gate_checklist(_context(), "anthropic", "sk-test") == ["A"]
    with patch("airi.ai_guide_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps(["B"])}]}}]})
        assert generate_gate_checklist(_context(), "google", "AIza-test") == ["B"]
    try:
        generate_gate_checklist(_context(), "openai", "sk-test")
        assert False, "should have raised — openai has no ai_guide_provider integration"
    except AiGuideUnavailable:
        pass
    print("OK: generate_gate_checklist_dispatches_by_provider")


def test_network_error_raises_clean():
    import httpx as httpx_module
    with patch("airi.ai_guide_provider.httpx.post", side_effect=httpx_module.ConnectTimeout("timed out")):
        try:
            generate_gate_checklist_anthropic(_context(), "sk-test")
            assert False, "should have raised"
        except AiGuideUnavailable as e:
            assert "could not reach" in str(e).lower()
            print("OK: network_error_raises_clean ->", e)


if __name__ == "__main__":
    test_has_ai_guide_provider()
    test_anthropic_returns_parsed_checklist()
    test_anthropic_error_response_raises_without_echoing_body()
    test_google_returns_parsed_checklist()
    test_google_error_response_raises_without_echoing_body()
    test_parse_checklist_tolerates_prose_and_code_fence()
    test_parse_checklist_filters_blanks_and_caps_length()
    test_parse_checklist_raises_on_unparseable_text()
    test_generate_gate_checklist_dispatches_by_provider()
    test_network_error_raises_clean()
    print("\nAll ai_guide_provider sanity checks passed.")
