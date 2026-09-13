-- Generic key/value settings table for admin-configurable runtime options
-- (currently just the Exact-mode test/BYOK toggle). Same convention as
-- other LivingIQ apps (PropertyIQ/AccidentIQ): one small table instead of
-- a dedicated column/migration per setting.
--
-- A missing row for a given key means "no admin override" — the app falls
-- back to an environment variable, then a hardcoded default. See
-- airi/runtime_config.py.

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
