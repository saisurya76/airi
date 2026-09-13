"""Quick sanity checks — not exhaustive, just enough to catch a broken build."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import analyze, list_supported_models


def test_basic_openai_prompt():
    r = analyze(prompt="Explain quantum computing simply.", model="gpt-4o", expected_output_tokens=500)
    # method is "tokenizer"/"high" when tiktoken can fetch its encoding table
    # (needs outbound network on first use, then it's cached locally), and
    # gracefully degrades to "heuristic"/"medium" when it can't reach it —
    # this sandbox blocks that one host, so expect either here.
    assert r.method in ("tokenizer", "heuristic")
    assert r.confidence in ("high", "medium")
    assert r.input_tokens > 0
    assert r.estimated_total_tokens == r.input_tokens + 500
    assert r.status == "SAFE"
    print("OK: basic_openai_prompt ->", r.to_dict())


def test_heuristic_fallback_for_non_openai_model():
    r = analyze(prompt="Hello there, how are you today?", model="claude-3-5-sonnet")
    assert r.method == "heuristic"
    assert r.confidence == "medium"
    assert r.input_tokens > 0
    print("OK: heuristic_fallback_for_non_openai_model ->", r.to_dict())


def test_unknown_model_is_low_confidence():
    r = analyze(prompt="test", model="some-made-up-model-9000")
    assert r.confidence == "low"
    assert r.known_model is False
    print("OK: unknown_model_is_low_confidence ->", r.to_dict())


def test_messages_input():
    r = analyze(
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What's the capital of France?"},
        ],
        model="gpt-4o-mini",
        expected_output_tokens=20,
    )
    assert r.input_tokens > 0
    print("OK: messages_input ->", r.to_dict())


def test_context_exceeded():
    huge_prompt = "word " * 20000  # way past gpt-4's 8192 window
    r = analyze(prompt=huge_prompt, model="gpt-4", expected_output_tokens=0)
    assert r.status == "EXCEEDED"
    print("OK: context_exceeded ->", r.status, r.context_utilization)


def test_requires_prompt_or_messages():
    try:
        analyze(model="gpt-4o")
        assert False, "should have raised"
    except ValueError:
        print("OK: requires_prompt_or_messages")


def test_supported_models_nonempty():
    models = list_supported_models()
    assert "gpt-4o" in models
    print("OK: supported_models_nonempty ->", models)


if __name__ == "__main__":
    test_basic_openai_prompt()
    test_heuristic_fallback_for_non_openai_model()
    test_unknown_model_is_low_confidence()
    test_messages_input()
    test_context_exceeded()
    test_requires_prompt_or_messages()
    test_supported_models_nonempty()
    print("\nAll sanity checks passed.")
