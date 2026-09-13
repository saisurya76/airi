-- Phase 2 of workspaces/projects (see docs/WORKSPACES.md): saved results
-- from running an existing AIRI tool inside a project. One row per run —
-- a project accumulates a history per tool rather than keeping only the
-- latest, since that history is what Phase 3's dashboard and Phase 4's
-- cross-project comparison will eventually read from.
--
-- Run once, after 001/002/003:
--   psql "$DATABASE_URL" -f sql/004_project_tool_runs.sql

CREATE TABLE IF NOT EXISTS project_tool_runs (
    id         BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tool       TEXT NOT NULL,               -- 'analyze' | 'exact' | 'project' | 'report'
    label      TEXT NOT NULL DEFAULT '',    -- optional, user-chosen (e.g. "Baseline check")
    -- The request body that produced this run, and the result it
    -- returned — both exactly as /analyze, /analyze/exact, /project, or
    -- /report already shape them (see airi/tool_runs.py). For an
    -- "exact" run, `input` never contains the BYOK key fields
    -- (anthropic_api_key/google_api_key) even if the request that
    -- created it did — those are stripped before this row is written,
    -- same never-store rule as /analyze/exact itself.
    input      JSONB NOT NULL,
    result     JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS project_tool_runs_lookup_idx
    ON project_tool_runs (project_id, tool, created_at DESC);

-- Not built yet (later phases, see docs/WORKSPACES.md):
--   005 — project_notes (append-only comment history, per project)
