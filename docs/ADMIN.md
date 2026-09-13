# Admin page: test mode, BYOK, and what's configured

`frontend/admin.html` is a small password-gated page for one deployment
setting — whether Exact mode (see [docs/EXACT_MODE.md](EXACT_MODE.md))
uses AIRI's own shared Anthropic/Gemini keys ("test mode") or requires
each signed-in user to bring their own key ("BYOK mode") — plus a
read-only checklist of which secrets this deployment has configured.
It's for you (or whoever runs this deployment), not for AIRI's regular
users.

## Why this exists

`ANTHROPIC_API_KEY`/`GOOGLE_API_KEY` (see docs/EXACT_MODE.md) were
originally AIRI's only way to make Exact-mode provider calls — one
shared key pool, sign-in just there to stop it being hammered. That's
fine for a demo or a low-traffic deployment, but it doesn't scale to
"anyone can sign in and use it" without either you eating everyone's
usage against your own key's rate limit, or deliberately keeping those
keys off outside of testing. BYOK mode is the other end of that
spectrum: no shared key at all, each user supplies their own, and
AIRI's own keys become purely a testing/demo convenience.

Because which mode makes sense can change (testing today, BYOK once
you have real users; back to testing for a demo), it's a runtime
toggle, not just an env var you set once at deploy time.

## Setup

Two new env vars, on top of everything in
[docs/EXACT_MODE.md](EXACT_MODE.md#one-time-setup) (`AUTH_SECRET` is
reused as-is — it now signs admin tokens too, not just user sessions):

- `ADMIN_PASSWORD` — any password you choose. There's no username and
  no hashing (it's compared with a constant-time check, same spirit as
  a per-request secret) — this matches the existing admin-page
  convention from PropertyIQ/AccidentIQ. Treat it like any shared
  password: whoever has it can flip test mode on this deployment.
  Without this set, the admin page's login (and every `/admin/*`
  endpoint) returns a clear `503` — the rest of AIRI is unaffected.
- `AIRI_TEST_MODE` — optional deployment default (`true`/`1`/`yes`/`on`
  or `false`/`0`/`no`/`off`). Defaults to **true** (test mode) if unset,
  so a fresh deployment with nothing configured is immediately usable
  in demo mode rather than failing closed on every Exact request.

Then open `/admin.html` on your deployed frontend and sign in with
`ADMIN_PASSWORD`.

## How the toggle is stored (precedence)

`airi/runtime_config.py` resolves test mode in this order, highest
priority first:

1. **An admin override in Postgres** (`app_config` table — apply
   `sql/002_app_config.sql` once, same as `sql/001_auth_schema.sql` in
   docs/EXACT_MODE.md). Set via the admin page (`POST /admin/config`).
   Survives restarts/redeploys.
2. **The `AIRI_TEST_MODE` env var**, if no admin override exists yet.
   Your deployment's own default — set once, left alone.
3. **A hardcoded default of `true`** if neither is set.

`GET /admin/config` reports which of the three is currently in effect
(`"source": "admin" | "env" | "default"`) so you can tell "an admin
turned this off" apart from "the deployment defaults to this."

If the database isn't configured (`DATABASE_URL` unset) or has a
hiccup, that's treated exactly like "no admin override exists" — the
env var/default still apply, and `/analyze/exact` is never taken down
by an admin-config read failing. Writing an override
(`POST /admin/config`) does need the database, though, and returns a
clear `503` if it's not configured.

## What signed-in users see

The frontend checks `GET /config` (public, no auth — just
`{"test_mode": bool}`) after loading, and only changes what a
**signed-in Exact-mode user** sees:

- **Test mode on:** a note that this deployment is using AIRI's own
  testing keys — nothing to configure.
- **Test mode off:** an optional Anthropic/Google API key panel. Keys
  are stored in that browser's `localStorage` only, sent with each
  `POST /analyze/exact` call, and never written to AIRI's own database,
  logged, or echoed back in any response — see "BYOK request shape"
  below. A "Manage keys" link next to "Signed in as ..." reopens the
  panel later to add, change, or clear a saved key.

Switching test mode off doesn't retroactively affect anyone already
signed in mid-request — the very next `/analyze/exact` call just starts
requiring (or stops requiring) a BYOK key, same as any other config
read that happens per-request.

## API reference

### `GET /config`

Public, unauthenticated. Returns `{"test_mode": bool}` — that's all;
it never reveals whether any secret is actually configured (that's
`GET /admin/config`, behind the admin password).

### `POST /admin/login`

Body: `{"password": "..."}`. Returns `{"token": "..."}` on success — a
signed JWT, **12-hour** expiry, distinct from a user's Exact-mode
session token (different claims; `airi/auth.py`'s `verify_session_token`
and `verify_admin_token` each reject the other's token type outright,
even though both are HS256-signed with the same `AUTH_SECRET`). `401`
on a wrong password, `503` if `ADMIN_PASSWORD` isn't set.

### `GET /admin/config`

Requires `Authorization: Bearer <admin token>`. Returns:

```json
{
  "test_mode": true,
  "source": "default",
  "configured": {
    "anthropic_key": true,
    "google_key": false,
    "resend": true,
    "database": true
  }
}
```

`configured` is a read-only checklist of whether each env var (pair, in
Resend's case) is set — never the values themselves.

### `POST /admin/config`

Requires the same admin token. Body: `{"test_mode": true}` (or
`false`). Writes the override to `app_config` and returns the same
shape as `GET /admin/config`'s `test_mode`/`source` fields, so the
admin page can confirm what actually took effect. `503` if the database
isn't configured.

## BYOK request shape

`POST /analyze/exact` accepts two new optional fields on top of the
usual `/analyze` body:

```json
{
  "prompt": "...",
  "model": "claude-3-5-sonnet",
  "anthropic_api_key": "sk-ant-...",
  "google_api_key": "AIza..."
}
```

Only the field matching the requested model's provider is ever used
for that call; the other is ignored. In **test mode**, both fields are
ignored entirely (AIRI's shared key is used instead) — sending them
does nothing, so the frontend simply doesn't send them while test mode
is on. In **BYOK mode**, the field matching the model's provider is
required; a missing one is a `400` naming which provider's key is
needed, not a `503` (this is a per-caller, fixable problem, not a
deployment one).

Neither key is ever stored, logged, or echoed back — each is used for
exactly the one provider call that request makes, then discarded with
the rest of the request body. If that provider call fails in BYOK mode
(bad key, provider outage, etc.), `/analyze/exact` degrades to the same
heuristic fallback as always, with a deliberately generic
`exact_mode_note` — never the provider's raw error text, to foreclose
any chance of a response ever echoing back something key-shaped.

## What this does *not* change

- `/analyze`, `/project`, `/report*` are completely unaffected —
  no auth, no test-mode dependency, exactly as documented elsewhere.
- Anyone not signed in still can't reach `/analyze/exact` at all,
  regardless of test mode — sign-in is a separate gate from BYOK.
- The core `airi` library has no notion of "test mode" — this is
  entirely an API-layer/frontend concern, same as auth and BYOK
  themselves.
