-- Adds Terms & Conditions acceptance tracking to users.
--
-- NULL means this user (whichever login path they used -- the owner's
-- email+OTP flow, or a team member's access code) hasn't accepted the
-- current terms yet. The frontend gates entry into the signed-in app
-- behind an explicit accept, recorded here with a timestamp -- an actual
-- audit trail of consent, not just a client-side localStorage flag (which
-- wouldn't survive a new browser/device, and wouldn't mean much as a
-- record that consent was actually given).
--
-- Additive and idempotent -- safe to run again.
--
-- Run once against your Neon database:
--   psql "$DATABASE_URL" -f sql/007_terms_acceptance.sql

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
