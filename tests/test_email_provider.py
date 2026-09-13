import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.email_provider import send_otp_email, EmailSendError


def test_missing_config_raises_clean_error():
    env = {k: v for k, v in os.environ.items() if k not in ("RESEND_API_KEY", "RESEND_FROM_EMAIL")}
    with patch.dict(os.environ, env, clear=True):
        try:
            send_otp_email("user@example.com", "042817")
            assert False, "should have raised"
        except EmailSendError as e:
            assert "isn't configured" in str(e).lower()
            print("OK: missing_config_raises_clean_error ->", e)


def test_successful_send_calls_resend_correctly():
    env = dict(os.environ, RESEND_API_KEY="re_test_key", RESEND_FROM_EMAIL="AIRI <otp@mail.example.com>")
    with patch.dict(os.environ, env):
        with patch("airi.email_provider.httpx.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            send_otp_email("user@example.com", "042817")

            assert mock_post.called
            args, kwargs = mock_post.call_args
            assert args[0] == "https://api.resend.com/emails"
            assert kwargs["headers"]["Authorization"] == "Bearer re_test_key"
            body = kwargs["json"]
            assert body["from"] == "AIRI <otp@mail.example.com>"
            assert body["to"] == ["user@example.com"]
            assert "042817" in body["text"]
            assert "042817" in body["html"]
            print("OK: successful_send_calls_resend_correctly")


def test_resend_error_response_raises_with_message():
    env = dict(os.environ, RESEND_API_KEY="re_test_key", RESEND_FROM_EMAIL="AIRI <otp@mail.example.com>")
    with patch.dict(os.environ, env):
        with patch("airi.email_provider.httpx.post") as mock_post:
            resp = MagicMock(status_code=422)
            resp.json.return_value = {"message": "domain is not verified"}
            mock_post.return_value = resp
            try:
                send_otp_email("user@example.com", "042817")
                assert False, "should have raised"
            except EmailSendError as e:
                assert "domain is not verified" in str(e)
                print("OK: resend_error_response_raises_with_message ->", e)


def test_network_failure_raises_clean_error():
    import httpx as httpx_module
    env = dict(os.environ, RESEND_API_KEY="re_test_key", RESEND_FROM_EMAIL="AIRI <otp@mail.example.com>")
    with patch.dict(os.environ, env):
        with patch("airi.email_provider.httpx.post") as mock_post:
            mock_post.side_effect = httpx_module.ConnectTimeout("timed out")
            try:
                send_otp_email("user@example.com", "042817")
                assert False, "should have raised"
            except EmailSendError as e:
                assert "could not reach" in str(e).lower()
                print("OK: network_failure_raises_clean_error ->", e)


if __name__ == "__main__":
    test_missing_config_raises_clean_error()
    test_successful_send_calls_resend_correctly()
    test_resend_error_response_raises_with_message()
    test_network_failure_raises_clean_error()
    print("\nAll email_provider sanity checks passed.")
