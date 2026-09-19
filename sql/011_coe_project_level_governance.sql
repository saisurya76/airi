-- CoE governance, take 3: adds a per-project override on top of the
-- workspace-level default from sql/010, and disambiguates the shared OTP
-- table so a step-up "confirm this sensitive change" code can never be
-- confused with a plain sign-in code for the same email.
--
-- THE MODEL CHANGES AGAIN, ON PURPOSE:
-- workspaces.coe_governance_enabled (sql/010) used to be a live gate for
-- every project in the workspace. As of this migration it's just the
-- INITIAL VALUE a brand-new project is created with -- not a live gate
-- any more. Each project now carries its own coe_governance_enabled,
-- set from the workspace's current switch at the moment the project is
-- created, and independently flippable afterwards by a workspace admin
-- (see PUT /projects/{id}/coe-governance in api.py). Turning the
-- workspace switch on/off later only changes what NEW projects default
-- to -- it no longer touches any existing project's own switch. This is
-- what makes "workspace creation sets the default, but the project
-- level can also be changed independently" true at the data level, not
-- just in the UI.
--
-- Both the workspace-level switch (PUT /workspaces/{id}/coe-governance)
-- and the project-level switch (PUT /projects/{id}/coe-governance) now
-- require a step-up email code to flip, not just being signed in as the
-- workspace admin -- see POST /auth/coe-governance/request-code. That
-- reuses the existing otp_codes table (sql/001), which needed a
-- `purpose` column added so a toggle-confirmation code and an ordinary
-- sign-in code for the same email are tracked as separate "latest
-- unconsumed code" lines (see get_latest_unconsumed_code in airi/db.py)
-- -- otherwise requesting one kind of code right after the other could
-- make the wrong one verify, or the right one appear not to exist.
--
-- Deliberately NOT included here (flagged, not decided yet): any rule
-- about WHEN a toggle is allowed -- e.g. whether turning governance off
-- should be blocked while a project has open Mandatory gates. For now
-- the toggle is unconditional once the step-up code is verified.
--
-- Run once, after 001-010:
--   psql "$DATABASE_URL" -f sql/011_coe_project_level_governance.sql

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS coe_governance_enabled BOOLEAN NOT NULL DEFAULT false;

-- Backfill: every existing project starts out matching its workspace's
-- current switch, so nothing changes in effect the moment this runs --
-- an admin can then diverge a specific project from here going forward.
UPDATE projects p
SET coe_governance_enabled = w.coe_governance_enabled
FROM workspaces w
WHERE p.workspace_id = w.id;

ALTER TABLE otp_codes
    ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'login';
