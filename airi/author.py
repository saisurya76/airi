"""
Author/founder profile shown on frontend/author.html — content only,
no auth of its own. Storage piggybacks on the same generic app_config
table runtime_config.py uses (see sql/002_app_config.sql): one row,
under the key AUTHOR_CONFIG_KEY, holding the whole profile as a JSON
blob. A dedicated table would be overkill for a single admin-edited
record with no relational structure.

Like airi/auth.py, airi/exact_provider.py, and airi/runtime_config.py,
this is an API-layer concern: part of the `airi` package for reuse,
never imported by airi/__init__.py.

Field validation lives here (pure functions, no I/O) so it's testable
without a database; airi/db.py is only touched by get_author_profile()/
set_author_profile() themselves.
"""

import json
import re
from typing import Any, Dict

from airi import db

AUTHOR_CONFIG_KEY = "author_profile"

# Every field is a plain string; "" means "not set" (never null/missing —
# callers, including the frontend, can always assume every key exists).
FIELDS = (
    "name", "title", "company", "tagline", "bio", "location",
    "email", "website", "linkedin", "twitter", "github", "photo_data_url",
)

# Short fields: a display line, not a paragraph. `bio` gets its own,
# much larger cap below. These are generous but bounded — this is a
# single admin-edited record, not user-submitted content, but Postgres
# text columns and page rendering both still deserve a sane ceiling.
SHORT_FIELD_MAX_CHARS = 200
BIO_MAX_CHARS = 4000

# A resized/compressed profile photo (see the client-side canvas resize
# in admin.html) should be well under 200KB — this cap is a generous
# backstop against an admin accidentally uploading something huge, not
# the expected size. ~2MB of base64 text in a Postgres row is trivial
# against Neon's free-tier 0.5GB cap, but a multi-MB "photo" bloating
# every GET /author response is still worth refusing outright.
MAX_PHOTO_DATA_URL_CHARS = 2_000_000

_PHOTO_DATA_URL_RE = re.compile(r"^data:image/(png|jpe?g|webp|gif);base64,[A-Za-z0-9+/]+=*$")


class AuthorProfileError(ValueError):
    """Raised for any invalid field — callers turn this into a 400."""


def default_profile() -> Dict[str, str]:
    return {field: "" for field in FIELDS}


def validate_profile(data: Dict[str, Any]) -> Dict[str, str]:
    """Takes a dict of (a subset of) FIELDS, returns a fully-populated,
    validated profile (missing fields default to ""). Raises
    AuthorProfileError with a message safe to show an admin on the
    first problem found."""
    result = default_profile()
    for field in FIELDS:
        if field not in data:
            continue
        value = data[field]
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise AuthorProfileError(f"'{field}' must be text.")

        if field == "bio":
            if len(value) > BIO_MAX_CHARS:
                raise AuthorProfileError(f"Bio is too long (max {BIO_MAX_CHARS} characters).")
        elif field == "photo_data_url":
            if value and not _PHOTO_DATA_URL_RE.match(value):
                raise AuthorProfileError("Photo must be a PNG/JPEG/WEBP/GIF image (uploaded via the admin page).")
            if len(value) > MAX_PHOTO_DATA_URL_CHARS:
                raise AuthorProfileError("Photo is too large — please use a smaller image.")
        else:
            if len(value) > SHORT_FIELD_MAX_CHARS:
                raise AuthorProfileError(f"'{field}' is too long (max {SHORT_FIELD_MAX_CHARS} characters).")

        result[field] = value
    return result


def get_author_profile() -> Dict[str, str]:
    """Returns the stored profile, or all-empty defaults if nothing's
    been set yet — including when the database isn't configured at all,
    same graceful-fallback spirit as runtime_config.get_test_mode()."""
    try:
        raw = db.get_config(AUTHOR_CONFIG_KEY)
    except db.DatabaseNotConfigured:
        return default_profile()
    if not raw:
        return default_profile()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        # Shouldn't happen (we're the only writer), but a corrupt row
        # should degrade to "no profile set" rather than a 500.
        return default_profile()
    profile = default_profile()
    if isinstance(parsed, dict):
        for field in FIELDS:
            value = parsed.get(field)
            if isinstance(value, str):
                profile[field] = value
    return profile


def set_author_profile(data: Dict[str, Any]) -> Dict[str, str]:
    """Validates `data`, stores it as the new profile (a full replace,
    same semantics as runtime_config.set_test_mode — not a merge), and
    returns what was actually saved."""
    profile = validate_profile(data)
    db.set_config(AUTHOR_CONFIG_KEY, json.dumps(profile))
    return profile
