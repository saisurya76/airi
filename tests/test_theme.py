import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import theme
from airi import db


def test_catalog_has_ten_themes_each_with_day_and_night():
    assert len(theme.THEMES) == 10
    required_keys = {"bg", "panel", "border", "text", "muted", "accent", "safe", "warning", "exceeded", "inset"}
    for t in theme.THEMES:
        assert set(t.keys()) == {"key", "label", "night", "day"}
        assert set(t["night"].keys()) == required_keys
        assert set(t["day"].keys()) == required_keys
    assert len(set(theme.THEME_KEYS)) == 10, "theme keys must be unique"
    print("OK: catalog_has_ten_themes_each_with_day_and_night")


def test_midnight_is_default_and_matches_existing_dark_look():
    assert theme.DEFAULT_THEME == "midnight"
    midnight = next(t for t in theme.THEMES if t["key"] == "midnight")
    # The existing hardcoded frontend values, unchanged by this feature.
    assert midnight["night"]["bg"] == "#0f1115"
    assert midnight["night"]["panel"] == "#171a21"
    assert midnight["night"]["accent"] == "#6ea8fe"
    assert midnight["night"]["inset"] == "#0f1115"
    print("OK: midnight_is_default_and_matches_existing_dark_look")


def test_validate_theme_key_accepts_known_rejects_unknown():
    assert theme.validate_theme_key("ocean") == "ocean"
    assert theme.validate_theme_key("") == theme.DEFAULT_THEME
    assert theme.validate_theme_key(None) == theme.DEFAULT_THEME
    try:
        theme.validate_theme_key("not_a_theme")
        assert False, "expected ValueError"
    except ValueError:
        pass
    print("OK: validate_theme_key_accepts_known_rejects_unknown")


def test_validate_time_str_accepts_24h_rejects_bad_shapes():
    assert theme.validate_time_str("06:00", "Day start") == "06:00"
    assert theme.validate_time_str("23:59", "Day start") == "23:59"
    for bad in ["6:00", "24:00", "12:60", "noon", "", "18:0"]:
        try:
            theme.validate_time_str(bad, "Day start")
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass
    print("OK: validate_time_str_accepts_24h_rejects_bad_shapes")


def test_get_theme_settings_defaults_when_nothing_configured():
    with patch.object(db, "get_config", return_value=None):
        settings = theme.get_theme_settings()
        assert settings == {
            "theme": "midnight",
            "auto_day_night": False,
            "day_start": "06:00",
            "night_start": "18:00",
        }
    print("OK: get_theme_settings_defaults_when_nothing_configured")


def test_get_theme_settings_database_not_configured_falls_back():
    with patch.object(db, "get_config", side_effect=db.DatabaseNotConfigured("no DB")):
        settings = theme.get_theme_settings()
        assert settings["theme"] == "midnight"
        assert settings["auto_day_night"] is False
    print("OK: get_theme_settings_database_not_configured_falls_back")


def test_get_theme_settings_reads_admin_overrides():
    stored = {
        "theme": "ocean",
        "theme_auto_day_night": "true",
        "theme_day_start": "07:30",
        "theme_night_start": "19:15",
    }
    with patch.object(db, "get_config", side_effect=lambda key: stored.get(key)):
        settings = theme.get_theme_settings()
        assert settings == {
            "theme": "ocean",
            "auto_day_night": True,
            "day_start": "07:30",
            "night_start": "19:15",
        }
    print("OK: get_theme_settings_reads_admin_overrides")


def test_set_theme_settings_validates_before_writing_anything():
    with patch.object(db, "set_config") as mock_set:
        try:
            theme.set_theme_settings("not_a_theme", True, "06:00", "18:00")
            assert False, "expected ValueError"
        except ValueError:
            pass
        mock_set.assert_not_called()

        try:
            theme.set_theme_settings("ocean", True, "6:00", "18:00")
            assert False, "expected ValueError"
        except ValueError:
            pass
        mock_set.assert_not_called()
    print("OK: set_theme_settings_validates_before_writing_anything")


def test_set_theme_settings_writes_through_db_set_config():
    with patch.object(db, "set_config") as mock_set, patch.object(db, "get_config", return_value=None):
        theme.set_theme_settings("grape", True, "07:00", "20:00")
        mock_set.assert_any_call("theme", "grape")
        mock_set.assert_any_call("theme_auto_day_night", "true")
        mock_set.assert_any_call("theme_day_start", "07:00")
        mock_set.assert_any_call("theme_night_start", "20:00")
    print("OK: set_theme_settings_writes_through_db_set_config")


if __name__ == "__main__":
    test_catalog_has_ten_themes_each_with_day_and_night()
    test_midnight_is_default_and_matches_existing_dark_look()
    test_validate_theme_key_accepts_known_rejects_unknown()
    test_validate_time_str_accepts_24h_rejects_bad_shapes()
    test_get_theme_settings_defaults_when_nothing_configured()
    test_get_theme_settings_database_not_configured_falls_back()
    test_get_theme_settings_reads_admin_overrides()
    test_set_theme_settings_validates_before_writing_anything()
    test_set_theme_settings_writes_through_db_set_config()
    print("\nAll theme sanity checks passed.")
