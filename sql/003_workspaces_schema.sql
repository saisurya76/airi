-- Workspaces/Projects (Phase 1): the relational structure behind
-- frontend/workspaces.html. Everything here is owned by a `users` row
-- (see sql/001_auth_schema.sql) — this whole feature only exists for a
-- signed-in ("Exact" flavor) user.
--
-- Run once, after 001 and 002:
--   psql "$DATABASE_URL" -f sql/003_workspaces_schema.sql

-- One row per user, created lazily on first use (not at signup). Holds
-- only a hash of the 4-digit "app key" (see airi/workspaces.py) used as
-- a re-confirmation PIN before destructive actions — never the key
-- itself, and never used as a real authentication credential (it's 4
-- digits; the session token is what actually authenticates every call).
CREATE TABLE IF NOT EXISTS user_profile (
    user_id      BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    app_key_hash TEXT,             -- NULL until the user has saved one
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspaces (
    id            BIGSERIAL PRIMARY KEY,
    owner_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    target        TEXT NOT NULL DEFAULT '',   -- high-level target of the exercise
    description   TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS workspaces_owner_idx ON workspaces (owner_user_id);

-- Team members invited into a workspace by email. As of Phase 2 (see
-- sql/006_workspace_member_access.sql, applied after this file) this is
-- a real second login identity, not just a notify-list — that migration
-- adds user_id/status/access_code_hash. Kept minimal here so 001-003
-- still describe the original Phase 1 shape as historical record.
CREATE TABLE IF NOT EXISTS workspace_members (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS workspace_members_unique_idx
    ON workspace_members (workspace_id, lower(email));

CREATE TABLE IF NOT EXISTS projects (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    -- One JSON object, one key per tech-stack category (see
    -- airi/workspaces.py: TECH_STACK_CATEGORIES). Kept as a flexible
    -- blob rather than a column per category so new categories can be
    -- added later without a migration; validated at the app layer
    -- (ai_services + ai_model are the two compulsory keys).
    tech_stack   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS projects_workspace_idx ON projects (workspace_id);

-- Later phases, see docs/WORKSPACES.md:
--   004 — project_tool_runs (saved results per AIRI tool tab — see
--         sql/004_project_tool_runs.sql)
--   005 — project_notes (append-only comment history, per project)
--   006 — real team-member access (roles, login codes, disable/enable —
--         see sql/006_workspace_member_access.sql)
