import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.auth import (
    AuthError,
    normalize_email,
    generate_code,
    hash_code,
    verify_code,
    create_session_token,
    verify_session_token,
    create_admin_token,
    verify_admin_token,
    extract_bearer_token,
    CODE_LENGTH,
)

PEPPER = "test-pepper"
SECRET = "test-jwt-secret"


def test_normalize_email_lowercases_and_strips():
    assert normalize_email("  Foo@Bar.COM  ") == "foo@bar.com"
    print("OK: normalize_email_lowercases_and_strips")


def test_normalize_email_rejects_garbage():
    for bad in ["", "not-an-email", "foo@", "@bar.com", "foo bar@baz.com"]:
        try:
            normalize_email(bad)
            assert False, f"should have rejected {bad!r}"
        except AuthError:
            pass
    print("OK: normalize_email_rejects_garbage")


def test_generate_code_shape():
    for _ in range(50):
        code = generate_code()
        assert len(code) == CODE_LENGTH
        assert code.isdigit()
    # Should not be trivially constant.
    codes = {generate_code() for _ in range(20)}
    assert len(codes) > 1
    print("OK: generate_code_shape")


def test_hash_and_verify_roundtrip():
    email = "user@example.com"
    code = "042817"
    h = hash_code(email, code, PEPPER)
    assert verify_code(email, code, PEPPER, h) is True
    print("OK: hash_and_verify_roundtrip")


def test_verify_rejects_wrong_code():
    email = "user@example.com"
    h = hash_code(email, "042817", PEPPER)
    assert verify_code(email, "000000", PEPPER, h) is False
    print("OK: verify_rejects_wrong_code")


def test_verify_rejects_cross_email_replay():
    # A hash minted for one email must not verify against a different one,
    # even with the same code and pepper — prevents replaying a leaked hash.
    code = "042817"
    h = hash_code("victim@example.com", code, PEPPER)
    assert verify_code("attacker@example.com", code, PEPPER, h) is False
    print("OK: verify_rejects_cross_email_replay")


def test_verify_rejects_malformed_input():
    h = hash_code("user@example.com", "042817", PEPPER)
    for bad in ["", "12345", "1234567", "abcdef", None]:
        assert verify_code("user@example.com", bad, PEPPER, h) is False
    print("OK: verify_rejects_malformed_input")


def test_session_token_roundtrip():
    token = create_session_token("user@example.com", SECRET)
    email = verify_session_token(token, SECRET)
    assert email == "user@example.com"
    print("OK: session_token_roundtrip")


def test_session_token_rejects_wrong_secret():
    token = create_session_token("user@example.com", SECRET)
    try:
        verify_session_token(token, "a-different-secret")
        assert False, "should have raised"
    except AuthError:
        print("OK: session_token_rejects_wrong_secret")


def test_session_token_rejects_tampered_token():
    token = create_session_token("user@example.com", SECRET)
    tampered = token[:-2] + ("aa" if token[-2:] != "aa" else "bb")
    try:
        verify_session_token(tampered, SECRET)
        assert False, "should have raised"
    except AuthError:
        print("OK: session_token_rejects_tampered_token")


def test_session_token_expired():
    # Build an already-expired token by hand (bypassing the fixed 30-day TTL)
    # to test the expiry path without sleeping for real.
    import jwt as pyjwt
    now = int(time.time())
    payload = {"sub": "user@example.com", "iat": now - 1000, "exp": now - 500}
    expired_token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    try:
        verify_session_token(expired_token, SECRET)
        assert False, "should have raised"
    except AuthError as e:
        assert "expired" in str(e).lower()
        print("OK: session_token_expired ->", e)


def test_admin_token_roundtrip():
    token = create_admin_token(SECRET)
    verify_admin_token(token, SECRET)  # raises on failure — no return value to check
    print("OK: admin_token_roundtrip")


def test_admin_token_rejects_wrong_secret():
    token = create_admin_token(SECRET)
    try:
        verify_admin_token(token, "a-different-secret")
        assert False, "should have raised"
    except AuthError:
        print("OK: admin_token_rejects_wrong_secret")


def test_admin_token_rejects_empty():
    try:
        verify_admin_token("", SECRET)
        assert False, "should have raised"
    except AuthError:
        print("OK: admin_token_rejects_empty")


def test_admin_token_expired():
    import jwt as pyjwt
    now = int(time.time())
    payload = {"sub": "admin", "role": "admin", "iat": now - 1000, "exp": now - 500}
    expired_token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    try:
        verify_admin_token(expired_token, SECRET)
        assert False, "should have raised"
    except AuthError as e:
        assert "expired" in str(e).lower()
        print("OK: admin_token_expired ->", e)


def test_admin_and_user_tokens_are_not_interchangeable():
    # The two token kinds are signed with the same secret and algorithm,
    # so this cross-check matters: an admin token must never authenticate
    # as a user session, and a user session token must never pass as an
    # admin token.
    admin_token = create_admin_token(SECRET)
    user_token = create_session_token("user@example.com", SECRET)

    try:
        verify_session_token(admin_token, SECRET)
        assert False, "admin token should not verify as a user session"
    except AuthError:
        pass

    try:
        verify_admin_token(user_token, SECRET)
        assert False, "user session token should not verify as an admin token"
    except AuthError:
        pass

    print("OK: admin_and_user_tokens_are_not_interchangeable")


def test_extract_bearer_token():
    assert extract_bearer_token("Bearer abc123") == "abc123"
    for bad in [None, "", "abc123", "Basic abc123"]:
        try:
            extract_bearer_token(bad)
            assert False, f"should have rejected {bad!r}"
        except AuthError:
            pass
    print("OK: extract_bearer_token")


if __name__ == "__main__":
    test_normalize_email_lowercases_and_strips()
    test_normalize_email_rejects_garbage()
    test_generate_code_shape()
    test_hash_and_verify_roundtrip()
    test_verify_rejects_wrong_code()
    test_verify_rejects_cross_email_replay()
    test_verify_rejects_malformed_input()
    test_session_token_roundtrip()
    test_session_token_rejects_wrong_secret()
    test_session_token_rejects_tampered_token()
    test_session_token_expired()
    test_admin_token_roundtrip()
    test_admin_token_rejects_wrong_secret()
    test_admin_token_rejects_empty()
    test_admin_token_expired()
    test_admin_and_user_tokens_are_not_interchangeable()
    test_extract_bearer_token()
    print("\nAll auth sanity checks passed.")
