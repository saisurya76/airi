-- AIRI "Exact" flavor: minimal email+OTP auth schema.
--
-- Deliberately small: two tables, no password, no phone, no session table.
-- A session is a signed JWT (see airi/auth.py), so there's nothing to look
-- up per request — keeps this cheap on a free-tier Postgres (Neon) even
-- under load, since the DB is only touched at login time, not per-analyze.
--
-- Run once against your Neon database:
--   psql "$DATABASE_URL" -f sql/001_auth_schema.sql

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

-- Case-insensitive uniqueness: "Foo@Bar.com" and "foo@bar.com" are the
-- same account. Enforced at the app layer (emails are lowercased before
-- every query) and backstopped here.
CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_idx ON users (lower(email));

CREATE TABLE IF NOT EXISTS otp_codes (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,      -- sha256(pepper + 6-digit code), never store the raw code
    expires_at  TIMESTAMPTZ NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    consumed    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast lookup of "the latest unconsumed code for this email" (verify) and
-- "how many codes has this email requested today" (rate limiting).
CREATE INDEX IF NOT EXISTS otp_codes_email_created_idx ON otp_codes (lower(email), created_at DESC);

-- Neon's free tier is 0.5 GB — old OTP rows are the only thing that grows
-- unbounded here. A cheap periodic cleanup (cron, or just call this
-- occasionally from the app) keeps that in check indefinitely.
-- DELETE FROM otp_codes WHERE created_at < now() - interval '7 days';
