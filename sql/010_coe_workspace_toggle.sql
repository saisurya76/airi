-- CoE Governance, take 2: replaces the `coe_initiative` project type +
-- coe_linked_project_id link (sql/008, sql/009) with a single workspace
-- -level switch. Reasoning: only a workspace admin can ever create a
-- project (see _require_workspace_admin), so "does this workspace do
-- CoE governance" is naturally a workspace decision, not something
-- expressed by adding a 4th project type per idea. With the switch on,
-- EVERY project in that workspace (api_request/license_request/
-- sdlc_request, same three as before CoE existed) carries the risk
-- tier/gates/roles/ledger; with it off, a project works exactly as it
-- did before this feature existed.
--
-- If any 'coe_initiative' rows exist from testing sql/008/009, delete
-- them before running this (the CHECK constraint below no longer
-- allows that value):
--   DELETE FROM projects WHERE project_type = 'coe_initiative';
--
-- Run once, after 001-009:
--   psql "$DATABASE_URL" -f sql/010_coe_workspace_toggle.sql

ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS coe_governance_enabled BOOLEAN NOT NULL DEFAULT false;

-- coe_linked_project_id only ever made sense for the now-removed
-- coe_initiative type; risk_tier/risk_factors/risk_explanation/
-- coe_roles/coe_phase_state on `projects` (from sql/008) are kept as-is
-- and now apply to any project type once the workspace switch is on.
ALTER TABLE projects DROP COLUMN IF EXISTS coe_linked_project_id;

DO $$
BEGIN
    ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_project_type_check;
    ALTER TABLE projects
        ADD CONSTRAINT projects_project_type_check
            CHECK (project_type IN ('api_request', 'license_request', 'sdlc_request'));
END $$;
