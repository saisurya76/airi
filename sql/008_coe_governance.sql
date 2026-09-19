-- CoE Governance (Workspaces Phase 6, first slice): a 4th project type,
-- `coe_initiative`, for tracking an AI initiative through the risk-tiered
-- model in the "CoE Phases -> AIRI Workspaces Projects" plan doc — see
-- airi/workspaces.py (RISK_FACTORS, COE_GATES, ACCOUNTABLE_ROLES) for the
-- logic this data feeds.
--
-- Deliberately lean, matching that plan's "breeze to use" goal: five new
-- columns on `projects` (all harmlessly blank/unused for the other three
-- project types, same convention as `tech_stack`), plus one new
-- append-only table for the decision ledger — no new tables for the
-- catalogs themselves (risk factors, gates, roles), which stay
-- server-defined Python constants, same pattern as TECH_STACK_CATEGORIES.
--
-- Run once, after 001-007:
--   psql "$DATABASE_URL" -f sql/008_coe_governance.sql

ALTER TABLE projects
    -- NULL until the Frame gate's risk form is answered (only meaningful
    -- for project_type = 'coe_initiative'); 'low' / 'standard' / 'high'.
    ADD COLUMN IF NOT EXISTS risk_tier TEXT,
    -- The 4 raw answers behind risk_tier (see airi/workspaces.py:
    -- RISK_FACTORS) — kept alongside the computed tier so the risk form
    -- can be re-opened pre-filled, and so a later recompute (e.g. the
    -- CoE Agent escalating a tier from live telemetry) has something to
    -- diff against.
    ADD COLUMN IF NOT EXISTS risk_factors JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- One-sentence "why" for the current tier (worst-factor-wins — see
    -- compute_risk_tier), stored rather than recomputed on every read so
    -- the UI never has to duplicate that logic.
    ADD COLUMN IF NOT EXISTS risk_explanation TEXT NOT NULL DEFAULT '',
    -- Who holds each of the 3 accountable roles for this initiative:
    -- {"business_owner": <user_id>, "technical_owner": <user_id>,
    -- "governance_owner": <user_id>}. Missing/null keys resolve to the
    -- workspace admin at read time — see airi/workspaces.py:
    -- resolve_coe_roles — so a brand-new initiative needs zero setup.
    ADD COLUMN IF NOT EXISTS coe_roles JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Current-state cache, one entry per gate key (see
    -- airi/workspaces.py: COE_GATES): {"<gate_key>": {"status": "...",
    -- "note": "...", "updated_at": "..."}}. History of how it got there
    -- lives in coe_control_events below, not here.
    ADD COLUMN IF NOT EXISTS coe_phase_state JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_project_type_check;
    ALTER TABLE projects
        ADD CONSTRAINT projects_project_type_check
            CHECK (project_type IN ('api_request', 'license_request', 'sdlc_request', 'coe_initiative'));
END $$;

DO $$
BEGIN
    ALTER TABLE projects
        ADD CONSTRAINT projects_risk_tier_check
            CHECK (risk_tier IS NULL OR risk_tier IN ('low', 'standard', 'high'));
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- constraint already added by a prior run of this migration
END $$;

-- Append-only decision ledger: one row per risk-tier set/change, gate
-- status change, or role assignment. `coe_phase_state`/`risk_tier`/
-- `coe_roles` above are current-state caches, fine for "what's true
-- right now" but wrong for "who decided what, when, why" once a value
-- can be overwritten — same current-state-plus-ledger split AIRI
-- already uses for projects/project_tool_runs.
CREATE TABLE IF NOT EXISTS coe_control_events (
    id             BIGSERIAL PRIMARY KEY,
    project_id     BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    gate_key       TEXT,               -- NULL for a risk-tier or role event (not gate-specific)
    event_type     TEXT NOT NULL,      -- 'risk_set' | 'gate_status_changed' | 'role_assigned'
    actor_user_id  BIGINT REFERENCES users(id),
    from_value     TEXT NOT NULL DEFAULT '',
    to_value       TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS coe_control_events_project_idx ON coe_control_events (project_id, created_at DESC);
