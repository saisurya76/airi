-- Live AI Guide per gate: an opt-in, BYOK-only AI-generated checklist for
-- a project's current CoE gate, saved so it survives a page reload
-- instead of being regenerated every time the Governance tab is opened.
--
-- One JSONB column, keyed by gate_key, shaped like:
--   {"design": {"items": ["...", "..."], "generated_at": "2026-...", "provider": "anthropic"}, ...}
-- Written via an atomic `||` merge (see set_project_gate_ai_guide in
-- airi/db.py), not a read-modify-write like the other CoE setters in
-- sql/008 -- two admins regenerating different gates' guides at close to
-- the same time is a real scenario this feature introduces, and a plain
-- read-modify-write could silently drop one of their writes.
--
-- This is unrelated to coe_governance_enabled (sql/010/011): a project
-- with governance off has no gates to guide in the first place (the
-- endpoint 400s via _require_coe_governance_enabled, same as coe-risk/
-- coe-roles/coe-phases), so there's nothing here to gate further.
--
-- Run once, after 001-011:
--   psql "$DATABASE_URL" -f sql/012_coe_gate_ai_guide.sql

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS coe_gate_ai_guides JSONB NOT NULL DEFAULT '{}'::jsonb;
