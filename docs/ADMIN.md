# Admin page: test mode, BYOK, deployment config, and the author profile

`frontend/admin.html` is a small password-gated page covering everything
about *running* this AIRI deployment rather than using it: whether
Exact mode (see [docs/EXACT_MODE.md](EXACT_MODE.md)) uses AIRI's own
shared Anthropic/Gemini keys ("test mode") or requires each signed-in
user to bring their own key ("BYOK mode"), a read-only checklist of
which secrets this deployment has configured, and the public founder/
author profile shown on `frontend/author.html`. It's for you (or
whoever runs this deployment), not for AIRI's regular users.

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

Public, unauthenticated. Returns `test_mode`, the site-visibility
flags, and the current appearance settings (`theme`,
`theme_auto_day_night`, `theme_day_start`, `theme_night_start` — see
"Appearance (themes)" below). It never reveals whether any secret is
actually configured (that's `GET /admin/config`, behind the admin
password).

### `GET /theme-catalog`

Public, unauthenticated. Returns `{"themes": [...], "default_theme":
"midnight"}` — the full theme catalog, so no frontend page hardcodes a
single color. See "Appearance (themes)" below.

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
  },
  "visibility": { "show_author_link": true, "show_license_wizard": true, "show_sdlc_wizard": true },
  "theme": { "theme": "midnight", "auto_day_night": false, "day_start": "06:00", "night_start": "18:00" }
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

### `POST /admin/theme`

Requires the admin token. Body `{"theme": "ocean", "auto_day_night":
true, "day_start": "06:00", "night_start": "18:00"}` — a full replace,
same convention as `POST /admin/config`: the admin page always sends
all four current values together. `400` if `theme` isn't one of the 10
catalog keys, or `day_start`/`night_start` isn't a 24-hour `"HH:MM"`
time. `503` if the database isn't configured. See "Appearance
(themes)" below.

### `GET /author`

Public, no auth. Returns the current founder/author profile — see
"Author profile" below — as all-empty strings if nothing's been set.
Used by both `frontend/author.html` (to render it) and the admin page
(to prefill the edit form).

### `POST /admin/author`

Requires the admin token. Body is the full profile (see below) — this
is a **full replace**, same semantics as `POST /admin/config`: a field
left out is saved as `""`, not left unchanged. `400` if any field fails
validation (a bad photo, something too long) — nothing is written when
that happens, so a rejected save can't half-overwrite a good profile.
`503` if the database isn't configured.

## Author profile

`frontend/author.html` is a small public page — a founder/about page —
built entirely from one admin-edited record: name, title, company,
tagline, bio, location, email, website, LinkedIn, X/Twitter, GitHub,
and a photo. Storage reuses the same `app_config` table as the
test-mode toggle (one row, key `"author_profile"`, value a JSON blob —
see `airi/author.py`) rather than a dedicated table, since it's a
single record with no relational structure.

Every field is optional and independent — leaving one blank just hides
that part of the page (no title/company → no role line; no photo → a
circular initial in its place; no links → no links row). There's
nothing to "turn on"; the page always reflects whatever's currently
saved, and shows a plain "no profile set up yet" state if nothing has
been.

**Validation** (`airi/author.py`, enforced server-side regardless of
what the admin UI already checks client-side):

| Field | Limit |
|---|---|
| `name`, `title`, `company`, `tagline`, `location`, `email`, `website`, `linkedin`, `twitter`, `github` | 200 characters |
| `bio` | 4,000 characters |
| `photo_data_url` | Must be a `data:image/{png,jpeg,webp,gif};base64,...` URL (SVG deliberately excluded — it can carry embedded script); capped at ~2MB of base64 text |

The admin page's photo upload resizes and re-encodes the image
client-side (max 480px on the longer side, JPEG at 85% quality) before
it ever reaches this validation, so a phone photo doesn't turn into a
multi-MB row — the size cap above is a backstop, not the expected case.

**Rendering safety**: `website`/`linkedin`/`twitter`/`github` are
free-text server-side (only length-checked, so an admin *could* type
`javascript:...` into one) — `frontend/author.html` is what actually
enforces that a link is only ever rendered if it starts with `http://`
or `https://`, and only renders an `email` value as a `mailto:` link if
it's shaped like an email address. Anything else is simply omitted
from the links row rather than shown as broken or unsafe.

## Appearance (themes)

`frontend/admin.html`'s "Appearance" panel picks a color theme for the
whole site (all 14 static pages), from a fixed catalog of 10 themes
defined in `airi/theme.py` — `THEMES`. Each theme has a **day** and a
**night** variant, ten CSS custom properties each (`bg`, `panel`,
`border`, `text`, `muted`, `accent`, `safe`, `warning`, `exceeded`,
`inset`) — the same properties every page already declares in its
`:root`. "Midnight" (the default) is exactly AIRI's original,
unchanged dark look; picking no theme at all is identical to picking
Midnight.

**How a page applies it**: every one of the 14 pages has a small script
in `<head>` that fetches `GET /config` (current settings) and `GET
/theme-catalog` (the palette values) in parallel, then calls
`document.documentElement.style.setProperty("--<name>", value)` for
each of the 10 properties — no page hardcodes a single hex value. This
runs before `<body>` to keep the flash of the default look brief, and
fails open (keeps the default look) on any error, same convention as
the site-visibility check.

**Day/night switching**: off by default (`theme_auto_day_night:
false`) — nothing changes for an existing deployment until an admin
turns it on. Once on, each page re-evaluates every 60 seconds, using
the *visitor's own local clock* (there's no single time zone for a
site with visitors anywhere), against the admin-set `day_start`/
`night_start` times (24-hour `"HH:MM"`, default `"06:00"`/`"18:00"`).

**Storage**: four more rows in the existing `app_config` table (no new
migration) — `theme`, `theme_auto_day_night`, `theme_day_start`,
`theme_night_start` — same "missing row = default" convention as the
test-mode toggle and the visibility flags.

**Adding an 11th theme** is a Python-only change: add an entry to
`THEMES` in `airi/theme.py` (10 day colors + 10 night colors); no
frontend file needs touching.

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

## Users list + demo/test data

Two more admin-page panels, both needing `sql/013_admin_demo_users.sql`
(run once, after `001`–`012`, the same way as every other migration in
this repo: `psql "$DATABASE_URL" -f sql/013_admin_demo_users.sql`). It
adds two columns to `users`:

- `is_demo` — true only for the handful of accounts the "Seed demo
  data" button creates. Every demo action below targets rows by this
  flag, and only this flag — never by matching on email — so there is
  no way for a demo action to reach a real user's account.
- `disabled` — a generic, reusable per-account kill switch, unrelated
  to a workspace membership's own status (`workspace_members.status`,
  `sql/006`). Checked once, centrally, in `api.py`'s
  `_require_session_email_and_user_id` — the function nearly every
  signed-in endpoint routes through — so disabling an account blocks
  an already-issued session token immediately, not just that account's
  next fresh login. (`GET /auth/me` is the one exception: it resolves
  the user id a different way and doesn't reflect `disabled` — a known,
  narrow gap, since the endpoints that matter for actually blocking
  access all go through the central check.)

### Users panel

`GET /admin/users` (admin token required) returns every account,
newest first, each with a usage rollup:

```json
[
  {
    "id": 7, "email": "someone@example.com",
    "created_at": "...", "last_login_at": "...",
    "is_demo": false, "disabled": false,
    "workspace_count": 2, "project_count": 5,
    "total_runs": 41, "total_tokens": 812345, "total_cost": 3.62
  }
]
```

`total_runs`/`total_tokens`/`total_cost` are summed across *every*
workspace and project that user owns, using the exact same
`airi.consolidated_report.aggregate_totals` a user's own Dashboard and
Comparison tabs use one level down (one workspace at a time) — so the
admin's number can never silently disagree with what that user sees
themselves. The admin page's Users table renders this straight, with
`demo`/`disabled` badges next to the email.

### Demo data panel

One seeded workspace, two accounts (`demo-owner@airi-demo.local`,
`demo-member@airi-demo.local` — fixed, non-receivable addresses, both
flagged `is_demo`), five projects covering every project type
(API-request, license-request, SDLC-request), saved tool runs (Standard
analyze, Traffic projection, Load-test report — see `airi/demo_seed.py`
for why **Exact mode is deliberately never seeded**: it's the one tool
whose entire value is a real, provider-verified count, and faking one
would put a number on screen that looks verified but isn't), notes, and
CoE governance turned ON for every one of the 5 projects, all 6 gates
touched on each, spanning all 3 risk tiers: support_copilot (Standard)
is a healthy, mostly-cleared lifecycle; fraud_triage (High) is
genuinely blocked — Verify stays flagged rather than cleared, so the
Mandatory-gate identity-enforcement restriction (only the resolved
accountable role can clear Design/Verify/Release) is visible without
extra setup; analytics_dashboard/license_request (Low) are fully
cleared straight through, showing how lightweight governance is once
nothing's actually risky; sdlc_request (Standard) is left mid-flow, a
pilot still in progress. The two governed-with-explicit-roles projects
(support_copilot, fraud_triage) also contrast with the other three,
which leave every role unassigned on purpose — demonstrating that a
solo/small workspace needs no role setup at all, since an unassigned
role just defaults to the workspace admin.

- **Seed demo data** — `POST /admin/demo/seed`. Idempotent in spirit:
  if the demo owner already owns a workspace, it's reused rather than
  duplicated (`{"created": false, ...}`), and no new member access code
  is issued in that case (see below). A fresh seed returns
  `member_access_code` once — write it down or use "Open demo session"
  before doing anything else, since it isn't recoverable afterward
  (only its hash is stored, same as any real access code).
- **Open demo session** — `POST /admin/demo/login`. Mints a real
  session JWT for the demo owner directly (`create_session_token`) and
  opens `app.html` in a new tab with it — no OTP round trip, since
  `demo-owner@airi-demo.local` can't receive one. This works because
  AIRI sessions are self-contained signed JWTs with no server-side
  session table (see `sql/001_auth_schema.sql`), so minting one for any
  email needs no database write — safe here specifically because the
  endpoint sits behind the same admin password as the rest of
  `/admin/*`, not because of anything new. The new tab arrives at
  `app.html?demo_token=...&demo_email=...`; both `app.html` and
  `workspaces.html` read those query params once on load
  (`bootstrapDemoSessionFromUrl`), store them under the same
  `localStorage` keys a normal sign-in uses, and strip them from the
  URL — the token is then validated against `GET /auth/me` exactly like
  any other stored session, so a stale or disabled token still gets
  rejected normally rather than trusting the URL blindly.
- **Disable / Enable** — `POST /admin/demo/disable` /
  `POST /admin/demo/enable`. There are two independent login paths in
  this app (the owner's OTP/session-token path, and the demo member's
  access-code path), and disabling the demo identity has to block both
  at once: `disable` sets `users.disabled` for both demo accounts *and*
  sets every related `workspace_members.status` to `"disabled"` in the
  same call, so neither login path is left open. Re-enabling flips both
  back.
- **Delete demo data (keep accounts)** — `DELETE /admin/demo/data`.
  Deletes the demo owner's workspace(s) — `ON DELETE CASCADE` takes
  every project, tool run, note, and CoE event underneath it with it —
  but keeps both demo accounts, so "Seed demo data" can build a fresh
  workspace for them again without minting new accounts.
- **Delete demo accounts entirely** — `DELETE /admin/demo/user`.
  Deletes the `users` rows themselves; the same cascade chain wipes
  everything they own in one statement. A later "Seed demo data" then
  creates brand-new demo accounts from scratch.

Building on top of the seeded data (adding projects, running more tool
runs, inviting a real teammate into the demo workspace) is expected and
safe — nothing about it is protected from normal editing, only from
being silently duplicated by re-seeding.

## What this does *not* change

- `/analyze`, `/project`, `/report*` are completely unaffected —
  no auth, no test-mode dependency, exactly as documented elsewhere.
- Anyone not signed in still can't reach `/analyze/exact` at all,
  regardless of test mode — sign-in is a separate gate from BYOK.
- The core `airi` library has no notion of "test mode" — this is
  entirely an API-layer/frontend concern, same as auth and BYOK
  themselves.
