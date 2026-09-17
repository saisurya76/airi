import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import runtime_config
from airi import db


def _clean_env():
    return {k: v for k, v in os.environ.items() if k != "AIRI_TEST_MODE"}


def test_default_when_nothing_configured():
    with patch.dict(os.environ, _clean_env(), clear=True):
        with patch.object(db, "get_config", return_value=None):
            value, source = runtime_config.get_test_mode()
            assert value is True
            assert source == "default"
    print("OK: default_when_nothing_configured")


def test_env_var_used_when_no_admin_override():
    for raw, expected in [("true", True), ("1", True), ("yes", True), ("on", True),
                           ("false", False), ("0", False), ("no", False), ("off", False)]:
        env = dict(_clean_env(), AIRI_TEST_MODE=raw)
        with patch.dict(os.environ, env, clear=True):
            with patch.object(db, "get_config", return_value=None):
                value, source = runtime_config.get_test_mode()
                assert value is expected, f"AIRI_TEST_MODE={raw!r} should give {expected}"
                assert source == "env"
    print("OK: env_var_used_when_no_admin_override")


def test_admin_override_wins_over_env_var():
    env = dict(_clean_env(), AIRI_TEST_MODE="true")
    with patch.dict(os.environ, env, clear=True):
        with patch.object(db, "get_config", return_value="false"):
            value, source = runtime_config.get_test_mode()
            assert value is False
            assert source == "admin"
    print("OK: admin_override_wins_over_env_var")


def test_database_not_configured_falls_back_gracefully():
    # A DB hiccup (or DATABASE_URL simply unset) must never bubble up as
    # an exception from get_test_mode — it should behave exactly like "no
    # admin override exists" and fall through to the env var / default.
    with patch.dict(os.environ, _clean_env(), clear=True):
        with patch.object(db, "get_config", side_effect=db.DatabaseNotConfigured("no DB")):
            value, source = runtime_config.get_test_mode()
            assert value is True
            assert source == "default"
    print("OK: database_not_configured_falls_back_gracefully")


def test_set_test_mode_writes_through_db_set_config():
    with patch.object(db, "set_config") as mock_set:
        runtime_config.set_test_mode(False)
        mock_set.assert_called_once_with(runtime_config.TEST_MODE_KEY, "false")
        mock_set.reset_mock()
        runtime_config.set_test_mode(True)
        mock_set.assert_called_once_with(runtime_config.TEST_MODE_KEY, "true")
    print("OK: set_test_mode_writes_through_db_set_config")


def test_visibility_default_true_when_nothing_configured():
    with patch.object(db, "get_config", return_value=None):
        assert runtime_config.get_visibility("show_author_link") is True
        assert runtime_config.get_all_visibility() == {
            "show_author_link": True,
            "show_license_wizard": True,
            "show_sdlc_wizard": True,
        }
    print("OK: visibility_default_true_when_nothing_configured")


def test_visibility_admin_override():
    with patch.object(db, "get_config", return_value="false"):
        assert runtime_config.get_visibility("show_license_wizard") is False
    with patch.object(db, "get_config", return_value="true"):
        assert runtime_config.get_visibility("show_license_wizard") is True
    print("OK: visibility_admin_override")


def test_visibility_database_not_configured_falls_back_to_default():
    with patch.object(db, "get_config", side_effect=db.DatabaseNotConfigured("no DB")):
        assert runtime_config.get_visibility("show_sdlc_wizard") is True
    print("OK: visibility_database_not_configured_falls_back_to_default")


def test_set_visibility_writes_through_db_set_config():
    with patch.object(db, "set_config") as mock_set:
        runtime_config.set_visibility("show_author_link", False)
        mock_set.assert_called_once_with("show_author_link", "false")
        mock_set.reset_mock()
        runtime_config.set_visibility("show_sdlc_wizard", True)
        mock_set.assert_called_once_with("show_sdlc_wizard", "true")
    print("OK: set_visibility_writes_through_db_set_config")


def test_visibility_rejects_unknown_key():
    try:
        runtime_config.get_visibility("not_a_real_flag")
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        runtime_config.set_visibility("not_a_real_flag", True)
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("OK: visibility_rejects_unknown_key")


if __name__ == "__main__":
    test_default_when_nothing_configured()
    test_env_var_used_when_no_admin_override()
    test_admin_override_wins_over_env_var()
    test_database_not_configured_falls_back_gracefully()
    test_set_test_mode_writes_through_db_set_config()
    test_visibility_default_true_when_nothing_configured()
    test_visibility_admin_override()
    test_visibility_database_not_configured_falls_back_to_default()
    test_set_visibility_writes_through_db_set_config()
    test_visibility_rejects_unknown_key()
    print("\nAll runtime_config sanity checks passed.")
