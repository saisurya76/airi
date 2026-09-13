"""
Phase 2 of workspaces/projects (see docs/WORKSPACES.md): saving a run of
an existing AIRI tool (Standard analyze, Exact mode, traffic projection,
load-test report) inside a project.

Pure logic only, like airi/auth.py and airi/workspaces.py — no network
calls, no DB access. The actual tool calls (analyze()/project()/
build_report()/the Exact-mode provider call) already live in the core
library and api.py; this module only validates the small amount that's
new here (which tool, what label) and shapes what gets persisted.
API-layer concern: part of the `airi` package for reuse, never imported
by airi/__init__.py.
"""

from enum import Enum


class ToolRunError(ValueError):
    """Raised for any user-facing validation failure here — callers turn
    this into a 400."""


class ToolName(str, Enum):
    """The four existing AIRI tools, now runnable (and saved) inside a
    project. Matches FastAPI's Enum-typed-path-param support directly —
    a request for an unlisted tool name is automatically a 422, no
    manual validation needed in api.py."""

    analyze = "analyze"
    exact = "exact"
    project = "project"
    report = "report"


LABEL_MAX_CHARS = 200


def validate_label(label: str) -> str:
    """A run's label is always optional — "" is a perfectly valid,
    common case (most runs won't be individually named). Only enforces
    the length cap."""
    label = (label or "").strip()
    if len(label) > LABEL_MAX_CHARS:
        raise ToolRunError(f"Label is too long (max {LABEL_MAX_CHARS} characters).")
    return label
