-- CoE Governance, part 2: a `coe_initiative` project doesn't capture a
-- new idea of its own — it governs one that's already been captured as
-- an api_request/license_request/sdlc_request project. This links the
-- two instead of leaving "which idea is this?" as a free-text guess.
--
-- Run once, after 001-009:
--   psql "$DATABASE_URL" -f sql/009_coe_linked_project.sql

ALTER TABLE projects
    -- Only meaningful for project_type = 'coe_initiative' — NULL for the
    -- other three types. ON DELETE SET NULL rather than CASCADE: if the
    -- governed project is deleted, the initiative's ledger (who decided
    -- what, when) is worth keeping even though the link it decided about
    -- is now dangling — the frontend shows "(deleted)" in that case.
    ADD COLUMN IF NOT EXISTS coe_linked_project_id BIGINT REFERENCES projects(id) ON DELETE SET NULL;
