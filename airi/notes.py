"""
Pure validation logic for a project's notes/comments history (Phase 3).
No DB access here — see airi/db.py for the project_notes CRUD and
api.py for the endpoints that call this.
"""


class NoteError(ValueError):
    """Raised for any user-facing validation failure here — callers turn
    this into a 400."""


NOTE_MAX_CHARS = 5000


def validate_note_body(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise NoteError("A note can't be empty.")
    if len(body) > NOTE_MAX_CHARS:
        raise NoteError(f"Note is too long (max {NOTE_MAX_CHARS} characters).")
    return body
