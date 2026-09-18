-- Adds a project_type classification to `projects` (see
-- docs/WORKSPACES.md): every project is now one of three request
-- types — the original "api_request" (token/cost analysis via
-- Standard/Exact/Traffic/Load-test, the only kind that existed before
-- this migration) or the two newer demo wizards' types,
-- "license_request" and "sdlc_request" (see airi/workspaces.py:
-- PROJECT_TYPES). Existing rows default to 'api_request', since that's
-- what every project was, implicitly, before this column existed.
--
-- Run once, after 001-006:
--   psql "$DATABASE_URL" -f sql/007_project_type.sql

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS project_type TEXT NOT NULL DEFAULT 'api_request';

DO $$
BEGIN
    ALTER TABLE projects
        ADD CONSTRAINT projects_project_type_check
            CHECK (project_type IN ('api_request', 'license_request', 'sdlc_request'));
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- constraint already added by a prior run of this migration
END $$;
