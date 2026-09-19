"""
Site-wide appearance: a catalog of named color themes (each with a
"day" and a "night" variant) plus the admin-configurable settings that
pick one and decide whether the page should switch between its day and
night variant automatically.

Same layering as runtime_config.py, reusing the same app_config table
(sql/002_app_config.sql) rather than a new migration — four more
key/value rows, not a new table:
  - "theme"                 -> one of THEME_KEYS
  - "theme_auto_day_night"  -> "true"/"false"
  - "theme_day_start"       -> "HH:MM", 24h, when the day variant starts
  - "theme_night_start"     -> "HH:MM", 24h, when the night variant starts
A missing row for any of the four means "use the default" — same
"no override yet" convention as get_test_mode()/get_visibility().

THEMES is served to the frontend as-is (GET /theme-catalog, public, no
auth — same spirit as GET /projects/tech-stack-categories): the 14
static pages never hardcode a single hex value, they ask the API for
the current settings (GET /config) and the full catalog, then set the
9 existing CSS custom properties plus --inset at runtime. Adding an
11th theme later is a Python-only change; no frontend file touches this
file's actual color values.

Like runtime_config.py and airi/workspaces.py, this is an API-layer
concern: part of the `airi` package for reuse, but never imported by
airi/__init__.py.
"""

import re
from typing import Any, Dict

from airi import db

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

# Ten named themes. Each variant is the same 10 keys the frontend's CSS
# already declares in :root (bg/panel/border/text/muted/accent/safe/
# warning/exceeded), plus "inset" — a 10th token this feature introduces
# for what was, until now, a hardcoded #0f1115 used across every page
# for "sunken" surfaces (inputs, stat tiles, history rows, progress-bar
# tracks). Hardcoding it meant those elements didn't change with a
# theme at all, and specifically broke light/day variants: dark input
# boxes with dark (var(--text)) text on top of them, unreadable. See
# docs/THEMES.md for the full list of frontend files that hardcoded
# background: #0f1115 and were switched to var(--inset).
#
# "midnight"/night is exactly today's existing look (unchanged hex
# values) so picking no theme at all, or picking Midnight, changes
# nothing about how the site already looks.
THEMES = [
    {
        "key": "midnight",
        "label": "Midnight",
        "night": {"bg": "#0f1115", "panel": "#171a21", "border": "#262a33", "text": "#e8e9ec",
                   "muted": "#9aa0ac", "accent": "#6ea8fe", "safe": "#3ecf8e", "warning": "#f2b84b",
                   "exceeded": "#f2555a", "inset": "#0f1115"},
        "day": {"bg": "#f7f8fa", "panel": "#ffffff", "border": "#e2e5ea", "text": "#1b1e24",
                 "muted": "#6b7280", "accent": "#2f6fed", "safe": "#17a673", "warning": "#b8790f",
                 "exceeded": "#d64545", "inset": "#eef0f3"},
    },
    {
        "key": "ocean",
        "label": "Ocean",
        "night": {"bg": "#071a24", "panel": "#0e2733", "border": "#163b49", "text": "#e6f3f7",
                   "muted": "#8fb4c0", "accent": "#35c2e0", "safe": "#2fd39a", "warning": "#f0b64c",
                   "exceeded": "#f2626a", "inset": "#071a24"},
        "day": {"bg": "#eef8fb", "panel": "#ffffff", "border": "#cfe6ee", "text": "#0b2530",
                 "muted": "#547985", "accent": "#0090b8", "safe": "#159669", "warning": "#b6790f",
                 "exceeded": "#d84a52", "inset": "#e2f1f6"},
    },
    {
        "key": "forest",
        "label": "Forest",
        "night": {"bg": "#0e150f", "panel": "#172218", "border": "#253524", "text": "#e6ede7",
                   "muted": "#93a596", "accent": "#6fcf8e", "safe": "#4fd17d", "warning": "#e7b653",
                   "exceeded": "#f0666a", "inset": "#0e150f"},
        "day": {"bg": "#f4f8f3", "panel": "#ffffff", "border": "#dbe6d8", "text": "#16241a",
                 "muted": "#5c715e", "accent": "#2f9e56", "safe": "#1f9d5c", "warning": "#ad7d16",
                 "exceeded": "#d24b4f", "inset": "#e9f1e7"},
    },
    {
        "key": "sunset",
        "label": "Sunset",
        "night": {"bg": "#1a1013", "panel": "#26161c", "border": "#3a2129", "text": "#f3e6e8",
                   "muted": "#b28c93", "accent": "#f2825a", "safe": "#4fd19a", "warning": "#f0b84e",
                   "exceeded": "#f2555a", "inset": "#1a1013"},
        "day": {"bg": "#fff6f0", "panel": "#ffffff", "border": "#f3ddce", "text": "#2b1712",
                 "muted": "#8a6c60", "accent": "#d9622f", "safe": "#17966d", "warning": "#b57310",
                 "exceeded": "#cf4b4b", "inset": "#fbeade"},
    },
    {
        "key": "grape",
        "label": "Grape",
        "night": {"bg": "#140f1c", "panel": "#201830", "border": "#322447", "text": "#ece7f5",
                   "muted": "#a598b8", "accent": "#a56ef2", "safe": "#4fd19a", "warning": "#f0b84e",
                   "exceeded": "#f2666a", "inset": "#140f1c"},
        "day": {"bg": "#f8f5fc", "panel": "#ffffff", "border": "#e3d8f0", "text": "#201530",
                 "muted": "#786a8a", "accent": "#7c3fd6", "safe": "#159669", "warning": "#b07914",
                 "exceeded": "#cc4a52", "inset": "#efe8f7"},
    },
    {
        "key": "slate",
        "label": "Slate",
        "night": {"bg": "#12151a", "panel": "#1b1f26", "border": "#2a2f38", "text": "#e6e9ee",
                   "muted": "#99a1ac", "accent": "#7c9cf0", "safe": "#3ecf8e", "warning": "#f0b750",
                   "exceeded": "#f2585c", "inset": "#12151a"},
        "day": {"bg": "#f5f6f8", "panel": "#ffffff", "border": "#dde1e7", "text": "#1a1d22",
                 "muted": "#666e7a", "accent": "#3f5fd6", "safe": "#189060", "warning": "#ad7c14",
                 "exceeded": "#cd4a4e", "inset": "#eaecef"},
    },
    {
        "key": "rose",
        "label": "Rose",
        "night": {"bg": "#180f13", "panel": "#261620", "border": "#3a2131", "text": "#f5e6ef",
                   "muted": "#b294a5", "accent": "#f26fa0", "safe": "#4fd19a", "warning": "#f0b750",
                   "exceeded": "#f2555c", "inset": "#180f13"},
        "day": {"bg": "#fff5f8", "panel": "#ffffff", "border": "#f3d9e4", "text": "#2b1620",
                 "muted": "#8a6a76", "accent": "#d63e83", "safe": "#159669", "warning": "#ad7c14",
                 "exceeded": "#cc4a52", "inset": "#fbe9f0"},
    },
    {
        "key": "amber",
        "label": "Amber",
        "night": {"bg": "#16130c", "panel": "#241f14", "border": "#382f1e", "text": "#f2ecdf",
                   "muted": "#b3a888", "accent": "#f2b84b", "safe": "#4fd19a", "warning": "#f5cc5e",
                   "exceeded": "#f2666a", "inset": "#16130c"},
        "day": {"bg": "#fdf8ef", "panel": "#ffffff", "border": "#ecdfc0", "text": "#2b2211",
                 "muted": "#8a7a52", "accent": "#b8790f", "safe": "#159669", "warning": "#a5720e",
                 "exceeded": "#cc4a52", "inset": "#f7efd9"},
    },
    {
        "key": "steel",
        "label": "Steel",
        "night": {"bg": "#0b0f14", "panel": "#131a22", "border": "#202a35", "text": "#edf1f5",
                   "muted": "#9fadbc", "accent": "#5fb0ee", "safe": "#45d3a0", "warning": "#f0b84e",
                   "exceeded": "#f45b60", "inset": "#0b0f14"},
        "day": {"bg": "#f2f6f9", "panel": "#ffffff", "border": "#d7e0e8", "text": "#101820",
                 "muted": "#5b6c7a", "accent": "#1f7cc4", "safe": "#128a5f", "warning": "#a3730f",
                 "exceeded": "#c8464c", "inset": "#e6edf2"},
    },
    {
        "key": "mono",
        "label": "Mono",
        "night": {"bg": "#0d0d0d", "panel": "#1a1a1a", "border": "#2b2b2b", "text": "#ededed",
                   "muted": "#a0a0a0", "accent": "#8f9bab", "safe": "#6fcf97", "warning": "#e0b25a",
                   "exceeded": "#e2686c", "inset": "#0d0d0d"},
        "day": {"bg": "#fafafa", "panel": "#ffffff", "border": "#dedede", "text": "#141414",
                 "muted": "#6e6e6e", "accent": "#46536b", "safe": "#1f9d5c", "warning": "#a5720e",
                 "exceeded": "#c8464c", "inset": "#f0f0f0"},
    },
]

THEME_KEYS = tuple(t["key"] for t in THEMES)
DEFAULT_THEME = "midnight"
DEFAULT_AUTO_DAY_NIGHT = False  # off by default: nothing changes for an existing deployment until an admin opts in
DEFAULT_DAY_START = "06:00"
DEFAULT_NIGHT_START = "18:00"

_THEME_KEY = "theme"
_AUTO_DAY_NIGHT_KEY = "theme_auto_day_night"
_DAY_START_KEY = "theme_day_start"
_NIGHT_START_KEY = "theme_night_start"


def validate_theme_key(value: Any) -> str:
    value = (value or "").strip() or DEFAULT_THEME
    if value not in THEME_KEYS:
        raise ValueError(f"Unknown theme: {value!r}.")
    return value


def validate_time_str(value: Any, field_label: str) -> str:
    value = (value or "").strip()
    if not _TIME_RE.match(value):
        raise ValueError(f"{field_label} must be a 24-hour time like \"06:00\".")
    return value


def get_theme_settings() -> Dict[str, Any]:
    """Returns {"theme", "auto_day_night", "day_start", "night_start"}.
    Any row missing (including "no database configured at all") falls
    back to its default — same as get_test_mode()/get_visibility()."""

    def _read(key: str):
        try:
            return db.get_config(key)
        except db.DatabaseNotConfigured:
            return None

    theme = _read(_THEME_KEY) or DEFAULT_THEME
    auto_raw = _read(_AUTO_DAY_NIGHT_KEY)
    auto_day_night = DEFAULT_AUTO_DAY_NIGHT if auto_raw is None else auto_raw.strip().lower() in ("1", "true", "yes", "on")
    day_start = _read(_DAY_START_KEY) or DEFAULT_DAY_START
    night_start = _read(_NIGHT_START_KEY) or DEFAULT_NIGHT_START
    return {
        "theme": theme,
        "auto_day_night": auto_day_night,
        "day_start": day_start,
        "night_start": night_start,
    }


def set_theme_settings(theme: str, auto_day_night: bool, day_start: str, night_start: str) -> Dict[str, Any]:
    """Full replace of all four settings — same "always send the whole
    form" convention as set_visibility()/AdminVisibilityBody. Raises
    ValueError (turned into a 400 by the caller) before writing anything
    if any field is invalid."""
    theme = validate_theme_key(theme)
    day_start = validate_time_str(day_start, "Day start time")
    night_start = validate_time_str(night_start, "Night start time")

    db.set_config(_THEME_KEY, theme)
    db.set_config(_AUTO_DAY_NIGHT_KEY, "true" if auto_day_night else "false")
    db.set_config(_DAY_START_KEY, day_start)
    db.set_config(_NIGHT_START_KEY, night_start)
    return get_theme_settings()
