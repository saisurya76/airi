import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi import author
from airi import db

TINY_PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_default_profile_has_every_field_empty():
    profile = author.default_profile()
    assert set(profile.keys()) == set(author.FIELDS)
    assert all(v == "" for v in profile.values())
    print("OK: default_profile_has_every_field_empty")


def test_validate_profile_fills_in_missing_fields():
    result = author.validate_profile({"name": "Surya"})
    assert result["name"] == "Surya"
    assert result["bio"] == ""
    assert set(result.keys()) == set(author.FIELDS)
    print("OK: validate_profile_fills_in_missing_fields")


def test_validate_profile_accepts_valid_photo():
    result = author.validate_profile({"photo_data_url": TINY_PNG_DATA_URL})
    assert result["photo_data_url"] == TINY_PNG_DATA_URL
    print("OK: validate_profile_accepts_valid_photo")


def test_validate_profile_rejects_non_image_data_url():
    for bad in ["not-a-data-url", "data:text/html;base64,PHNjcmlwdD4=", "javascript:alert(1)", "data:image/svg+xml;base64,PHN2Zz4="]:
        try:
            author.validate_profile({"photo_data_url": bad})
            assert False, f"should have rejected {bad!r}"
        except author.AuthorProfileError:
            pass
    print("OK: validate_profile_rejects_non_image_data_url")


def test_validate_profile_rejects_oversized_photo():
    huge = "data:image/png;base64," + ("A" * (author.MAX_PHOTO_DATA_URL_CHARS + 1))
    try:
        author.validate_profile({"photo_data_url": huge})
        assert False, "should have raised"
    except author.AuthorProfileError as e:
        assert "large" in str(e).lower()
        print("OK: validate_profile_rejects_oversized_photo ->", e)


def test_validate_profile_rejects_oversized_bio():
    try:
        author.validate_profile({"bio": "x" * (author.BIO_MAX_CHARS + 1)})
        assert False, "should have raised"
    except author.AuthorProfileError as e:
        assert "long" in str(e).lower()
        print("OK: validate_profile_rejects_oversized_bio ->", e)


def test_validate_profile_rejects_oversized_short_field():
    try:
        author.validate_profile({"name": "x" * (author.SHORT_FIELD_MAX_CHARS + 1)})
        assert False, "should have raised"
    except author.AuthorProfileError as e:
        assert "long" in str(e).lower()
        print("OK: validate_profile_rejects_oversized_short_field ->", e)


def test_validate_profile_rejects_non_string():
    try:
        author.validate_profile({"name": 12345})
        assert False, "should have raised"
    except author.AuthorProfileError:
        print("OK: validate_profile_rejects_non_string")


def test_validate_profile_ignores_unknown_fields():
    result = author.validate_profile({"name": "Surya", "not_a_real_field": "whatever"})
    assert "not_a_real_field" not in result
    print("OK: validate_profile_ignores_unknown_fields")


def test_get_author_profile_defaults_when_nothing_stored():
    with patch.object(db, "get_config", return_value=None):
        profile = author.get_author_profile()
        assert profile == author.default_profile()
    print("OK: get_author_profile_defaults_when_nothing_stored")


def test_get_author_profile_database_not_configured_falls_back():
    with patch.object(db, "get_config", side_effect=db.DatabaseNotConfigured("no DB")):
        profile = author.get_author_profile()
        assert profile == author.default_profile()
    print("OK: get_author_profile_database_not_configured_falls_back")


def test_get_author_profile_reads_stored_json():
    import json
    stored = json.dumps({"name": "Surya", "title": "Founder"})
    with patch.object(db, "get_config", return_value=stored):
        profile = author.get_author_profile()
        assert profile["name"] == "Surya"
        assert profile["title"] == "Founder"
        assert profile["bio"] == ""  # not in the stored JSON -> default
    print("OK: get_author_profile_reads_stored_json")


def test_get_author_profile_handles_corrupt_json():
    with patch.object(db, "get_config", return_value="{not valid json"):
        profile = author.get_author_profile()
        assert profile == author.default_profile()
    print("OK: get_author_profile_handles_corrupt_json")


def test_set_author_profile_validates_and_writes_through_db():
    with patch.object(db, "set_config") as mock_set:
        saved = author.set_author_profile({"name": "Surya", "title": "Founder"})
        assert saved["name"] == "Surya"
        assert mock_set.called
        args, kwargs = mock_set.call_args
        assert args[0] == author.AUTHOR_CONFIG_KEY
        import json
        written = json.loads(args[1])
        assert written["name"] == "Surya"
    print("OK: set_author_profile_validates_and_writes_through_db")


def test_set_author_profile_rejects_invalid_before_writing():
    with patch.object(db, "set_config") as mock_set:
        try:
            author.set_author_profile({"bio": "x" * (author.BIO_MAX_CHARS + 1)})
            assert False, "should have raised"
        except author.AuthorProfileError:
            pass
        assert not mock_set.called, "must not write anything when validation fails"
    print("OK: set_author_profile_rejects_invalid_before_writing")


if __name__ == "__main__":
    test_default_profile_has_every_field_empty()
    test_validate_profile_fills_in_missing_fields()
    test_validate_profile_accepts_valid_photo()
    test_validate_profile_rejects_non_image_data_url()
    test_validate_profile_rejects_oversized_photo()
    test_validate_profile_rejects_oversized_bio()
    test_validate_profile_rejects_oversized_short_field()
    test_validate_profile_rejects_non_string()
    test_validate_profile_ignores_unknown_fields()
    test_get_author_profile_defaults_when_nothing_stored()
    test_get_author_profile_database_not_configured_falls_back()
    test_get_author_profile_reads_stored_json()
    test_get_author_profile_handles_corrupt_json()
    test_set_author_profile_validates_and_writes_through_db()
    test_set_author_profile_rejects_invalid_before_writing()
    print("\nAll author sanity checks passed.")
