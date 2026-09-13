-- Phase 2: real team-member access (supersedes the "informational only"
-- note in sql/003_workspaces_schema.sql). Turns workspace_members from a
-- pure notify-list into an actual second login identity with its own
-- role-scoped access, per the workspace owner's spec — see
-- docs/WORKSPACES.md for the full write-up:
--
--   - Whoever creates a workspace is its "admin". The existing
--     owner_user_id column on `workspaces` already captures this — no
--     schema change needed for admins themselves.
--   - A team member gets full working access to every project inside
--     the workspace (run all 4 tools, save results, add/delete their
--     own notes, delete tool runs) but can never create/delete a
--     workspace or project, edit either's basic details, or manage
--     membership (those stay admin-only).
--   - A member signs in with their own persistent access code — set by
--     the admin when adding them — instead of the owner's email+OTP
--     flow. See airi/auth.py's generate_access_code/verify_access_code
--     and POST /auth/member-login in api.py. It's an ongoing login
--     credential, not a one-time invite: only its hash is ever stored.
--   - The admin can disable (and re-enable) a member's access without
--     removing them, and can regenerate a lost/compromised code.
--
-- Run once, after 001-005:
--   psql "$DATABASE_URL" -f sql/006_workspace_member_access.sql

ALTER TABLE workspace_members
    ADD COLUMN IF NOT EXISTS user_id           BIGINT REFERENCES users(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS access_code_hash  TEXT;   -- NULL for a pre-migration row until the admin regenerates one

DO $$
BEGIN
    ALTER TABLE workspace_members
        ADD CONSTRAINT workspace_members_status_check CHECK (status IN ('active', 'disabled'));
EXCEPTION
    WHEN duplicate_object THEN NULL;  -- constraint already added by a prior run of this migration
END $$;

CREATE INDEX IF NOT EXISTS workspace_members_user_idx ON workspace_members (user_id);
