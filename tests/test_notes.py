import pytest

from airi.notes import NOTE_MAX_CHARS, NoteError, validate_note_body


def test_trims_whitespace():
    assert validate_note_body("  looks good, ship it  ") == "looks good, ship it"


def test_blank_note_rejected():
    with pytest.raises(NoteError):
        validate_note_body("   ")


def test_missing_note_rejected():
    with pytest.raises(NoteError):
        validate_note_body("")


def test_oversized_note_rejected():
    with pytest.raises(NoteError):
        validate_note_body("x" * (NOTE_MAX_CHARS + 1))


def test_max_length_note_accepted():
    body = "x" * NOTE_MAX_CHARS
    assert validate_note_body(body) == body
