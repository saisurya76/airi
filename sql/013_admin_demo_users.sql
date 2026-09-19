-- Two small, generically useful additions to `users`, both introduced
-- for the admin-triggered demo/test data feature (see
-- airi/demo_seed.py, docs/ADMIN.md's "Demo data" section) but not
-- specific to it:
--
--   is_demo   -- true for the handful of accounts an admin seeds for
--               end-to-end demoing/testing, never set any other way.
--               Every admin action that disables/wipes/deletes "the
--               test user(s)" targets rows by this flag, and only this
--               flag -- never by email pattern-matching or any other
--               heuristic, so there is no way for a demo-cleanup action
--               to reach a real user's account by accident.
--   disabled  -- generic account-level kill switch, independent of a
--               workspace membership's own status (sql/006): blocks
--               issuing OR honoring a session for this email, checked
--               once, centrally, in api.py's
--               _require_session_email_and_user_id -- so disabling an
--               account takes effect immediately even against an
--               already-issued session token, not just future logins.
--               Used today only by the demo-disable action, but built
--               as a plain per-user column so it's available if this
--               app ever needs to disable a real, abusive account too.
--
-- Run once, after 001-012:
--   psql "$DATABASE_URL" -f sql/013_admin_demo_users.sql

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_demo  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS disabled BOOLEAN NOT NULL DEFAULT false;

-- Every demo-cleanup query filters on this, so it's worth an index even
-- though the row count here will always be tiny.
CREATE INDEX IF NOT EXISTS users_is_demo_idx ON users (is_demo) WHERE is_demo;
