import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.exact_provider import (
    count_tokens_anthropic,
    count_tokens_google,
    count_tokens_exact,
    has_exact_provider,
    ExactCountUnavailable,
)


def test_has_exact_provider():
    assert has_exact_provider("anthropic") is True
    assert has_exact_provider("google") is True
    assert has_exact_provider("openai") is False
    assert has_exact_provider("unknown") is False
    print("OK: has_exact_provider")


def test_anthropic_simple_prompt():
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"input_tokens": 42})
        n = count_tokens_anthropic("Hello there", None, "claude-3-5-sonnet", "sk-test")
        assert n == 42
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.anthropic.com/v1/messages/count_tokens"
        assert kwargs["headers"]["anthropic-version"] == "2023-06-01"
        assert kwargs["headers"]["X-Api-Key"] == "sk-test"
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "Hello there"}]
        assert "system" not in kwargs["json"]
        print("OK: anthropic_simple_prompt")


def test_anthropic_splits_system_message():
    messages = [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "user", "content": "How are you?"},
    ]
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"input_tokens": 100})
        n = count_tokens_anthropic(None, messages, "claude-3-5-sonnet", "sk-test")
        assert n == 100
        body = mock_post.call_args.kwargs["json"]
        assert body["system"] == "You are terse."
        assert body["messages"] == [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello."},
            {"role": "user", "content": "How are you?"},
        ]
        print("OK: anthropic_splits_system_message")


def test_anthropic_error_response_raises():
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=401, text="invalid x-api-key")
        try:
            count_tokens_anthropic("Hi", None, "claude-3-5-sonnet", "bad-key")
            assert False, "should have raised"
        except ExactCountUnavailable as e:
            assert "401" in str(e)
            print("OK: anthropic_error_response_raises ->", e)


def test_google_simple_prompt():
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"totalTokens": 7})
        n = count_tokens_google("The quick brown fox.", None, "gemini-1.5-flash", "AIza-test")
        assert n == 7
        args, kwargs = mock_post.call_args
        assert "models/gemini-1.5-flash:countTokens" in args[0]
        assert "key=AIza-test" in args[0]
        body = kwargs["json"]
        assert body["generateContentRequest"]["contents"][0]["parts"][0]["text"] == "The quick brown fox."
        assert body["generateContentRequest"]["contents"][0]["role"] == "user"
        print("OK: google_simple_prompt")


def test_google_maps_assistant_to_model_role():
    messages = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"totalTokens": 5})
        count_tokens_google(None, messages, "gemini-1.5-pro", "AIza-test")
        contents = mock_post.call_args.kwargs["json"]["generateContentRequest"]["contents"]
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        print("OK: google_maps_assistant_to_model_role")


def test_google_system_instruction_included():
    messages = [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}]
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"totalTokens": 9})
        count_tokens_google(None, messages, "gemini-1.5-flash", "AIza-test")
        body = mock_post.call_args.kwargs["json"]
        assert body["generateContentRequest"]["systemInstruction"]["parts"][0]["text"] == "Be brief."
        print("OK: google_system_instruction_included")


def test_google_falls_back_on_rich_shape_rejection():
    # First call (rich generateContentRequest shape) is rejected; second
    # call (plain contents-only shape) succeeds — should NOT raise, and
    # should still return the count from the successful retry.
    responses = [
        MagicMock(status_code=400, text="unknown field generateContentRequest"),
        MagicMock(status_code=200, json=lambda: {"totalTokens": 11}),
    ]
    with patch("airi.exact_provider.httpx.post", side_effect=responses) as mock_post:
        n = count_tokens_google("Hi", None, "gemini-1.5-flash", "AIza-test")
        assert n == 11
        assert mock_post.call_count == 2
        second_call_body = mock_post.call_args_list[1].kwargs["json"]
        assert "generateContentRequest" not in second_call_body
        assert "contents" in second_call_body
        print("OK: google_falls_back_on_rich_shape_rejection")


def test_google_raises_when_both_shapes_fail():
    responses = [
        MagicMock(status_code=400, text="bad request"),
        MagicMock(status_code=500, text="server error"),
    ]
    with patch("airi.exact_provider.httpx.post", side_effect=responses):
        try:
            count_tokens_google("Hi", None, "gemini-1.5-flash", "AIza-test")
            assert False, "should have raised"
        except ExactCountUnavailable as e:
            assert "500" in str(e)
            print("OK: google_raises_when_both_shapes_fail ->", e)


def test_count_tokens_exact_dispatches_by_provider():
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"input_tokens": 3})
        n = count_tokens_exact("Hi", None, "claude-3-5-sonnet", "anthropic", "sk-test")
        assert n == 3
    with patch("airi.exact_provider.httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200, json=lambda: {"totalTokens": 4})
        n = count_tokens_exact("Hi", None, "gemini-1.5-flash", "google", "AIza-test")
        assert n == 4
    try:
        count_tokens_exact("Hi", None, "gpt-4o", "openai", "sk-test")
        assert False, "should have raised — openai has no exact_provider function"
    except ExactCountUnavailable:
        pass
    print("OK: count_tokens_exact_dispatches_by_provider")


def test_network_error_raises_clean():
    import httpx as httpx_module
    with patch("airi.exact_provider.httpx.post", side_effect=httpx_module.ConnectTimeout("timed out")):
        try:
            count_tokens_anthropic("Hi", None, "claude-3-5-sonnet", "sk-test")
            assert False, "should have raised"
        except ExactCountUnavailable as e:
            assert "could not reach" in str(e).lower()
            print("OK: network_error_raises_clean ->", e)


if __name__ == "__main__":
    test_has_exact_provider()
    test_anthropic_simple_prompt()
    test_anthropic_splits_system_message()
    test_anthropic_error_response_raises()
    test_google_simple_prompt()
    test_google_maps_assistant_to_model_role()
    test_google_system_instruction_included()
    test_google_falls_back_on_rich_shape_rejection()
    test_google_raises_when_both_shapes_fail()
    test_count_tokens_exact_dispatches_by_provider()
    test_network_error_raises_clean()
    print("\nAll exact_provider sanity checks passed.")
