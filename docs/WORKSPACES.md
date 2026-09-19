# Workspaces & Projects (Phases 1–6)

`frontend/workspaces.html` lets a signed-in user organize their work
into **workspaces** (a team/initiative) containing **projects** (a
specific thing being analyzed), instead of using AIRI's tools as a
one-off calculator every time. This is a **multi-phase plan**: Phase 1
shipped the data model, CRUD, and full restore-on-login; Phase 2 made
AIRI's tools runnable and saved inside a project; Phase 3 added a
Dashboard tab, a Notes tab, and an Actions tab with a downloadable
consolidated PDF report; Phase 4 added a cross-project comparison tab
with its own PDF report, plus per-saved-run PDF downloads; Phase 5
gave a workspace's team members their own real, role-scoped access
instead of just an email notification; Phase 6 (see "CoE Governance"
below) adds a risk-tiered governance model, switched on per project
(defaulted from a workspace-level setting) rather than for a whole
workspace at once, with the on/off switch itself requiring a step-up
email code on top of being the workspace admin, and gives each gate a
Live AI Guide — an opt-in, BYOK-only AI-generated checklist tailored to
that specific project (see "Live AI Guide per gate" below). No further
phases are planned as of this delivery.

Like Exact mode, this entire feature requires a signed-in session — a
workspace's admin signs in with email + OTP (see
[docs/EXACT_MODE.md](EXACT_MODE.md)), a team member with the
workspace-scoped access code their admin gave them (`POST
/auth/member-login` — see "Team-member access" below). Neither touches
`/analyze`, `/project`, `/report*`, which remain completely anonymous
and stateless. Either login path is also subject to the one-time Terms
& Conditions gate described in
[docs/EXACT_MODE.md](EXACT_MODE.md#terms--conditions-gate) — it's
account-wide, not specific to this feature.

## Data model

Three new tables (`sql/003_workspaces_schema.sql`), on top of the
`users` table Exact mode already created:

- **`user_profile`** — one row per user, holding only a hash of their
  4-digit "app key" (see below). Created lazily on first save, not at
  signup.
- **`workspaces`** — owned by exactly one user (`owner_user_id`).
  `title` (required), `target` (the high-level target of the exercise),
  `description`.
- **`workspace_members`** — a workspace's team, by email. Phase 1
  treated this as purely informational (a notification email on
  add/remove, no real access). Phase 5 (`sql/006_workspace_member_access.sql`)
  turned it into a real second login identity — see "Team-member
  access" below.
- **`projects`** — belongs to one workspace. `title`, `description`,
  and `tech_stack` (a JSON object — see below). Deleting a workspace
  cascades to its members and projects.

## The app key: a confirmation PIN, not a password

Every destructive action — deleting a workspace or a project — requires
re-entering a 4-digit code the user chose ("the app key"), the same
spirit as typing a resource's name into a "type DELETE to confirm" box.

Design decisions worth calling out:

- **It is not a second authentication factor.** The session JWT from
  OTP sign-in is what actually authenticates every request; the app
  key only gates one action (delete) behind one extra confirmation.
  Because of that, it's fine for it to be short and simple — a real
  4-digit PIN would be a bad password, but it's an appropriate "are you
  sure" gate.
- **Only a hash is ever stored** (`user_profile.app_key_hash`, SHA-256
  bound to the user id + a server-side pepper — see
  `airi.workspaces.hash_app_key`). The digits themselves are never
  written to the database, logged, or returned by any endpoint —
  `GET /profile` only ever reports `{"has_app_key": true/false}`.
- **If no app key has ever been saved, every delete is blocked outright**
  (400: "Set an app key in your profile before deleting anything") —
  there's nothing to confirm against otherwise, so the safer default is
  refusing the action rather than silently allowing it.
- The frontend's mask/eye-icon toggle (`frontend/workspaces.html`)
  applies only to *what the user is currently typing* into the app-key
  input — there's no way to "reveal" a previously-saved key, by design,
  since the server never has the plaintext to give back.

## Tech stack categories

Every project records its tech stack as one JSON object
(`airi.workspaces.TECH_STACK_CATEGORIES`), one key per layer:

| Category | Required? |
|---|---|
| `frontend` | optional |
| `backend` (middle tier) | optional |
| `database` | optional |
| `ai_services` (AI service/provider) | **required** |
| `ai_model` | **required** |
| `hosting` (infrastructure) | optional |
| `cache_queue` | optional |
| `mobile` | optional |
| `other` | optional |

**Why only `ai_services`/`ai_model` are compulsory**: every project
AIRI can meaningfully analyze is, by definition, making calls to *some*
AI service and model — that's the one thing this tool can't work
without. A project might genuinely have no frontend (a backend batch
job) or no separate database, so nothing else is forced. `GET
/projects/tech-stack-categories` returns this table live, so the
frontend form (and any future client) never has to hardcode it
separately — add a category in `airi/workspaces.py` and it appears
there automatically.

## API reference

All endpoints below require `Authorization: Bearer <session token>`
(from `POST /auth/verify-code` for a workspace's admin, or `POST
/auth/member-login` for a team member — see "Team-member access"
below; the two produce identical, fully-capable session tokens) and
return `401` without one.

Every endpoint checks the caller's *relationship* to the workspace/
project, not just whether it exists:

- No relationship at all (not the owner, no membership row) → `404`,
  identically to an id that doesn't exist — never confirms another
  workspace/project id is valid to someone with no claim on it.
- An active team member hitting an admin-only action (see the table
  below) → `403` — a real relationship, just not the right one for
  that action.
- A *disabled* member, on anything → `403` — see "Team-member access".

### Profile

- `GET /profile` → `{"has_app_key": bool}`.
- `POST /profile/app-key` — body `{"app_key": "1234"}` (exactly 4
  digits). Sets or overwrites the key. `400` if not 4 digits.

### Workspaces

- `GET /workspaces` — every workspace this user can reach: the ones
  they own (`role: "admin"`) plus the ones where they're an *active*
  team member (`role: "member"`) — a disabled membership is excluded
  entirely, not just hidden. `member_count`/`project_count` per
  workspace, most recent first.
- `POST /workspaces` — body `{"title", "target", "description"}`
  (`title` required). Whoever creates a workspace is its admin.
  Returns the new workspace with empty `members`/`projects` arrays and
  `role: "admin"`.
- `GET /workspaces/{id}` — admin or active member. Full detail: the
  workspace fields plus `role`, `members`, and `projects` arrays —
  everything needed to restore the page on login in one call.
- `PUT /workspaces/{id}` — **admin-only** (`403` for a member). Same
  body shape as create; a full replace of the three editable fields.
- `DELETE /workspaces/{id}` — **admin-only**. Body `{"app_key":
  "1234"}`. Cascades to members and projects. `400` if the app key is
  missing/wrong/unset.

### Team members — **admin-only** (see "Team-member access" below)

- `POST /workspaces/{id}/members` — body `{"email": "..."}`. `409` if
  already a member (case-insensitive). Generates the member's access
  code, stores only its hash, and returns it in the response as
  `access_code` — **shown exactly once**, never retrievable again.
  Sends `send_member_added_email` (`airi/email_provider.py`)
  best-effort as a courtesy notification — it never contains the code.
- `DELETE /workspaces/{id}/members/{member_id}` — hard delete; the
  member's code stops working immediately. Sends
  `send_member_removed_email` the same way.
- `POST /workspaces/{id}/members/{member_id}/disable` /
  `.../enable` — toggles access without removing the member or their
  history; re-enabling needs no new code.
- `POST /workspaces/{id}/members/{member_id}/regenerate-code` — issues
  a fresh code (returned once, as `access_code`) and immediately
  invalidates the old one.

### Projects

- `GET /projects/tech-stack-categories` — public (no auth needed): the
  category table above, as JSON.
- `POST /workspaces/{id}/projects` — **admin-only**. Body `{"title",
  "description", "tech_stack": {...}}`. `400` if `title` is blank or
  `tech_stack` is missing `ai_services`/`ai_model`.
- `GET /projects/{id}` — admin or active member.
- `PUT /projects/{id}` — **admin-only** (basic details: title,
  description, tech stack).
- `DELETE /projects/{id}` — **admin-only**. Body `{"app_key": "1234"}`,
  same gate as deleting a workspace.

## Tool runs (Phase 2): AIRI's tools, inside a project

Each of AIRI's four existing tools is now runnable *and saved* inside a
project, in a tab of its own on `frontend/workspaces.html`: **Standard**
(`/analyze`), **Exact** (`/analyze/exact`), **Traffic** (`/project`,
volume projection), and **Load Test** (`/report`, load-test reporting).
Every one of these tool-run endpoints, plus notes, the dashboard, and
both report/comparison endpoints below, is **admin-or-active-member** —
a team member has full working access to everything inside a project
they can reach; only workspace/project identity and membership
management (above) are admin-only. See "Team-member access" below.

Every run is stored as one row in `project_tool_runs`
(`sql/004_project_tool_runs.sql`): which tool, an optional user-chosen
`label`, the exact request body (`input`) and the exact response
(`result`) — both JSONB, both shaped identically to the corresponding
stateless endpoint. A project accumulates a *history* per tool (most
recent first) rather than keeping only the latest run, since that
history is what Phase 3's dashboard and Phase 4's comparison tab will
eventually read from.

**One deliberate exception to "input is stored exactly as sent"**: an
Exact-tool run's `input` never contains `anthropic_api_key`/
`google_api_key`, even when the request that created it carried a BYOK
key (see docs/ADMIN.md). Those fields are stripped before the row is
ever written — `api.py`'s `_exact_input_for_storage` is the one place
that happens, so there's a single point to audit. A BYOK key remains
exactly as ephemeral as it was before this feature: used for one
provider call, never logged, never persisted, regardless of whether
that call happened via `/analyze/exact` directly or via a saved
project run.

The four stateless endpoints (`/analyze`, `/analyze/exact`, `/project`,
`/report`) are unaffected — this phase refactored their internals into
shared helpers (`_do_analyze`, `_do_exact`, `_do_project`,
`_build_report_or_400`) purely so a saved run and a plain call can
never silently drift apart, not to change either endpoint's behavior.

### API reference

All require `Authorization: Bearer <session token>`; a project id
belonging to another user returns `404`, same as elsewhere in this
feature.

| Endpoint | Purpose |
|---|---|
| `POST /projects/{id}/tools/analyze/runs` | Same body as `POST /analyze`, plus optional `label`. Runs it and saves the result. |
| `POST /projects/{id}/tools/exact/runs` | Same body as `POST /analyze/exact` (BYOK fields included, never persisted), plus optional `label`. Subject to the same per-user rate limit as `/analyze/exact`. |
| `POST /projects/{id}/tools/project/runs` | Same body as `POST /project` (an `archetypes` list), plus optional `label`. |
| `POST /projects/{id}/tools/report/runs` | Same body as `POST /report` (`run_name` + `records`), plus optional `label`. |
| `GET /projects/{id}/tools/{tool}/runs` | That tool's saved runs for this project, most recent first. `tool` is one of `analyze`/`exact`/`project`/`report` — anything else is a `422`. |
| `DELETE /projects/{id}/tools/{tool}/runs/{run_id}` | Body `{"app_key": "1234"}` — same delete-confirmation gate as everything else in this feature. |

Deleting a project cascades to its saved tool runs
(`ON DELETE CASCADE`), same as it already cascades to nothing else at
this level (members/projects cascade from workspaces, not from here).

## Dashboard, Notes & Actions (Phase 3)

Three more tabs on every project, all built directly on top of the
`project_tool_runs` history Phase 2 introduced:

- **Dashboard** — charts/figures rolled up across *every saved run in
  every tool tab*: a donut chart of saved-run counts by tool, a bar
  chart of estimated cost by tool, stat tiles (total saved runs, total
  estimated tokens, total estimated cost), and a SAFE/WARNING/EXCEEDED
  status-mix bar chart. It's plain inline SVG/CSS — no charting library
  — matching the rest of the app's dependency-free frontend. It
  re-fetches automatically the first time it's opened, and again right
  after any tool tab in the same project finishes a run, so it never
  shows stale numbers for a project you're actively working in.
- **Notes** — an append-only, timestamped comment history
  (`project_notes`, `sql/005_project_notes.sql`; validation in
  `airi/notes.py`). Add a note, and it joins a read-only list, most
  recent first; clicking an entry expands it in place to show the full
  text (the same expand-in-place pattern the tool-run history already
  uses). The only mutation is delete — gated by the same app-key
  confirmation as everything else destructive in this feature. There's
  no edit: a note is a timestamped entry in a history, not a document
  you revise.
- **Actions** — an on-screen consolidated summary (totals, a per-tool
  breakdown table, notes count) plus a **"Download consolidated report
  (PDF)"** button.

### The consolidated report: one aggregation, two views

`airi/consolidated_report.py` is pure logic that rolls up a project's
*entire* saved history (every tool run, not just the latest) plus its
notes into one shape — normalizing each tool's very different result
fields (`estimated_cost` vs `total_cost`, a real SAFE/WARNING/EXCEEDED
split for analyze/exact/report vs. just an `any_exceeded` flag for
traffic projections) into a common `{cost, tokens, status_counts}` per
run, then summing. The Actions tab's on-screen JSON
(`GET /projects/{id}/report/consolidated`) and its PDF
(`GET /projects/{id}/report/consolidated.pdf`, via the same
`xhtml2pdf` pipeline `/report/pdf` already uses) are both built from
this one aggregation — `api.py`'s `_build_consolidated_report` — so
they can never silently disagree. This is the same reasoning as
`report.py`/`report_render.py` for the standalone Load-test report.

**"Prepared by"**: this is the signed-in session's own email — the
person who happened to generate this particular report, admin or
active team member alike — not the project's original creator. There's
still no separate name field to pull from (the `users` table only ever
stores an email — see `sql/001_auth_schema.sql`), and no per-project
"created by" is tracked, so a consolidated/comparison report always
credits whoever pulled it up, which may differ from run to run once a
workspace has more than one person working in it.

One rough edge, worth calling out: the consolidated totals sum cost
across all four tools even though a Load-test report run is *itself*
already an aggregate over many synthetic requests. Treat the grand
total as an order-of-magnitude combined figure across everything saved
in a project, not a literal sum of independent charges.

### API reference (Phase 3)

| Endpoint | Purpose |
|---|---|
| `POST /projects/{id}/notes` | Body `{"body": "..."}` (1–5000 chars after trimming). Creates one note. |
| `GET /projects/{id}/notes` | This project's notes, most recent first. |
| `DELETE /projects/{id}/notes/{note_id}` | Body `{"app_key": "1234"}` — same delete-confirmation gate as everything else. |
| `GET /projects/{id}/report/consolidated` | The Actions/Dashboard tabs' aggregation as JSON: `project`, `prepared_by`, `generated_at`, `totals`, `by_tool`, `latest` (latest saved run per tool, or `null`), `notes`. |
| `GET /projects/{id}/report/consolidated.pdf` | The same data, rendered to a downloadable PDF. |

Notes cascade-delete with their project (`ON DELETE CASCADE`), same as
saved tool runs.

## Cross-project comparison & per-run PDFs (Phase 4)

Two things land together in this delivery: a **Project comparison**
section on the workspace page, and PDF downloads for individual saved
runs (not just a project's consolidated report).

- **Project comparison** — appears on the workspace page, above the
  project list, **once that workspace has 2+ projects** (a single
  project has nothing to compare against, so the section stays
  hidden). It shows stat tiles (projects compared, total saved runs,
  combined estimated cost/tokens), a donut chart of estimated cost by
  project, and a table ranking every project **most-expensive-first**,
  with saved-run count, tokens, cost, and a SAFE/WARNING/EXCEEDED
  status mix per project. Like the Dashboard tab, it re-fetches
  automatically any time a tool run is saved or deleted anywhere in
  the workspace, so it never goes stale while you're working.
- **Per-run PDF download** — every entry in a tool's run history
  (Standard, Exact, Traffic projection, Load-test report alike) now has
  its own **"Download PDF"** button next to "Delete this run", for
  sharing or filing a single saved result without pulling the whole
  project's consolidated report.

### Comparison: the same one-aggregation-backs-two-views pattern

`airi/comparison.py` is pure logic, and it deliberately reuses
`airi/consolidated_report.py`'s per-run normalization
(`run_stats`/`aggregate_totals`) rather than re-inventing it: each
project's summary is just that project's saved runs (across all four
tools) rolled up with the exact same logic Phase 3 already uses for a
single project, then `rank_by_cost` sorts those summaries
most-expensive-first, and `aggregate_workspace_totals` sums them into
the workspace-wide stat tiles. The on-screen JSON
(`GET /workspaces/{id}/comparison`) and the PDF
(`GET /workspaces/{id}/comparison.pdf`) are both built from
`api.py`'s `_build_workspace_comparison`, so — same reasoning as
Phase 3's consolidated report — they can never silently disagree.

**"Workspace-level reporting"**, from the original plan, is this
comparison tab plus its downloadable PDF: a workspace's report *is*
its projects compared side by side. The comparison PDF's "Prepared by"
line uses the signed-in session's own email — same reasoning as the
consolidated report (see above): admin or active team member alike,
whoever generated this particular report.

A single-project workspace still answers `GET
.../comparison` successfully (a degenerate `project_count: 1`
comparison) — the frontend is what decides to hide the section, not
the API, so anything else calling this endpoint directly still gets a
usable answer.

### Per-run PDF: reusing the Phase 3 renderers for one run instead of a whole project

`render_single_run_html` (added to `airi/consolidated_report.py`)
builds a standalone one-page PDF for exactly one saved run, reusing
the exact same per-tool result renderers
(`_render_latest_analyze_or_exact`, `_render_latest_project`,
`_render_latest_report`) that the consolidated report already uses to
show a tool's "latest run" section — so a run looks identical whether
it's shown inside the full consolidated report or downloaded on its
own. `GET /projects/{id}/tools/{tool}/runs/{run_id}/pdf` 404s if the
run doesn't belong to that project, or if `{tool}` in the URL doesn't
match the run's actual tool (guards against a stale/mismatched link).

### API reference (Phase 4)

| Endpoint | Purpose |
|---|---|
| `GET /projects/{id}/tools/{tool}/runs/{run_id}/pdf` | One saved run, rendered to a downloadable PDF. 404s on a project/tool/run mismatch. |
| `GET /workspaces/{id}/comparison` | Cross-project comparison as JSON: `workspace`, `prepared_by`, `generated_at`, `workspace_totals`, `projects` (each project's totals + by-tool breakdown, ranked most-expensive-first). |
| `GET /workspaces/{id}/comparison.pdf` | The same data, rendered to a downloadable PDF. |

## Team-member access (Phase 5)

Requirements, as given: whoever creates a workspace is its admin; a
team member gets full working access to a workspace's projects (run
every tool, save results, add/delete their own notes and tool runs)
but can never create or delete a workspace or project, edit either's
basic details, or manage membership; adding a member gives them an
admin-issued code to sign in and use AIRI/generate reports with; and
the admin can disable a member's access. `sql/006_workspace_member_access.sql`
extends `workspace_members` with `user_id`, `status`
(`'active'`/`'disabled'`), and `access_code_hash` to support this.

### Roles

Two roles, both derived per-request, never stored as a separate
"permissions" concept:

- **admin** — the workspace's `owner_user_id`. Exactly one per
  workspace, fixed at creation; ownership never transfers.
- **member** — a row in `workspace_members` for this user, with
  `status = 'active'`. A `'disabled'` row is a real relationship (so it
  reads as `403`, not `404`) that just doesn't grant access right now.

`api.py`'s `_get_accessible_workspace`/`_get_accessible_project` return
`(resource, role)` for every read; `_require_workspace_admin`/
`_require_project_admin` additionally 403 a non-admin caller, for the
admin-only actions listed in the API reference above. This is
deliberately a different helper from `_require_admin`, which gates the
site's own password-protected founder admin page — an unrelated
concept that happens to share the word "admin".

### The access code: a member's ongoing login, not a one-time invite

When an admin adds a member (`POST /workspaces/{id}/members`), the
backend:

1. Generates a random code (`airi.auth.generate_access_code` —
   10 characters, from an alphabet with no `0`/`O`/`1`/`I`/`L`, since an
   admin typically reads or pastes this to the member by hand rather
   than it being auto-emailed).
2. Resolves or creates the member's own `users` row immediately
   (`db.upsert_user_login`) — so there's a `user_id` to key permission
   checks on even before the member ever signs in.
3. Stores only the code's hash (`airi.auth.hash_code`, same
   email+pepper binding as the OTP/app-key hashes elsewhere in this
   codebase) alongside the new membership row.
4. Returns the **plaintext code exactly once**, as `access_code` in the
   response — never stored, logged, or included in the notification
   email. It is the admin's job to relay it to the member out of band.

The member then signs in with `POST /auth/member-login` (body
`{"email", "code"}`), which checks the code against every *active*
membership for that email (a member can belong to more than one
workspace, each with its own code) and, on a match, issues a normal
session token via the same `create_session_token` the OTP flow uses.
**There is no separate "session type"** — a member-login session is
byte-for-byte the same kind of JWT an OTP sign-in produces; every
workspace/project endpoint evaluates permission per-request from the
caller's resolved `user_id`, independent of which flow produced the
session. This was a deliberate choice over a one-time invite followed
by normal OTP sign-in: the code *is* the member's ongoing credential,
reusable indefinitely until the admin disables it or regenerates it.

`POST /workspaces/{id}/members/{member_id}/regenerate-code` replaces a
lost or compromised code the same way — a fresh code, returned once,
with the old one invalidated immediately (there's no way to recover
the old code to invalidate it any other way, since only its hash was
ever stored).

### Disable / enable: revocable without losing history

`POST /workspaces/{id}/members/{member_id}/disable` flips `status` to
`'disabled'` without touching the membership row otherwise — the
member's saved tool runs, notes, and history all stay exactly as they
are, and `.../enable` restores access with the *same* code (no
re-invite needed). Because every endpoint re-checks `status` on every
request, a disabled member is locked out immediately — their session
token is still cryptographically valid, but `GET /workspaces` silently
excludes the workspace from their list, and any direct request against
it 403s. Removing a member (`DELETE .../members/{member_id}`) is the
separate, irreversible option, for when the relationship itself is
over rather than just paused.

### API reference (Phase 5)

| Endpoint | Purpose |
|---|---|
| `POST /auth/member-login` | Body `{"email", "code"}`. `400` on a wrong email/code pair (never distinguishes which). Returns `{"token", "email", "terms_accepted"}`, same shape as `POST /auth/verify-code`. |
| `POST /workspaces/{id}/members/{member_id}/disable` | Admin-only. Revokes access, keeps history. |
| `POST /workspaces/{id}/members/{member_id}/enable` | Admin-only. Restores access with the existing code. |
| `POST /workspaces/{id}/members/{member_id}/regenerate-code` | Admin-only. New code (returned once as `access_code`), old one invalidated. |

(`POST`/`DELETE /workspaces/{id}/members` are documented under "Team
members" above — Phase 5 changed their behavior, not their shape.)

## CoE Governance (Phase 6)

A lightweight, risk-tiered governance model an initiative can run
through instead of a generic checklist — designed to need **zero
setup** for a low-risk project and to get genuinely hard to bypass for
a high-risk one. Governance is switched on and off **per project**
(`coe_governance_enabled` on `projects`) — every project, of any of the
3 types (`api_request`/`license_request`/`sdlc_request`), has its own
switch. A new project starts out at whatever its workspace's own
`coe_governance_enabled` default currently is; from then on a workspace
admin can flip that one project's switch independently, without
touching any of its siblings or the workspace default itself. Both
switches — the workspace-level default and a project's own — are
admin-only *and* require a fresh step-up email code to flip, on top of
being signed in as the admin (see "The two switches" below). With a
project's switch off, it works exactly as it did before this feature
existed; with it on, that project carries a risk tier, 6 gates,
accountable roles, and a decision ledger.

This went through three earlier shapes during development, all
superseded: a 4th `project_type` called `coe_initiative` (`sql/008`),
then a `coe_linked_project_id` column so that initiative could point at
an existing project (`sql/009`), then a single workspace-wide switch
with no per-project override (`sql/010`). `sql/011_coe_project_level_governance.sql`
adds the per-project switch on top of `sql/010`'s workspace switch (and
a `purpose` column on `otp_codes` — see "The two switches" below),
while *keeping* the five columns `sql/008` added to `projects`
(`risk_tier`, `risk_factors`, `risk_explanation`, `coe_roles`,
`coe_phase_state`) and the `coe_control_events` ledger table unchanged,
since those apply the same way regardless of which shape controls the
on/off switch. The catalogs themselves (risk factors, gates, roles) are
server-defined Python constants in `airi/workspaces.py`, not new
tables — same pattern as `TECH_STACK_CATEGORIES` — served
unauthenticated via `GET /projects/coe-catalog` so the frontend never
hardcodes them.

### The two switches

`workspaces.coe_governance_enabled` is only ever read at **project
creation time** now — it's what a brand-new project in that workspace
defaults to, nothing more. It's still settable directly (no step-up
code) on `POST /workspaces`, since a brand-new workspace has no
existing projects for a step-up confirmation to protect, but `PUT
/workspaces/{id}` (the ordinary title/target/description autosave) no
longer touches it at all.

`projects.coe_governance_enabled` is the switch that actually governs
a given project day to day — it's what `_require_coe_governance_enabled`
checks, and what the frontend's Governance tab reads to decide whether
to show the risk quiz or an "off" empty state. It starts out equal to
the workspace's switch at creation time (see `create_project` in
`api.py`), then lives entirely independently of it.

Both switches change only through their own dedicated, step-up-gated
endpoint — `PUT /workspaces/{id}/coe-governance` or `PUT
/projects/{id}/coe-governance` — never through the ordinary
title/target/description or title/description/tech-stack/type saves.
Flipping either one is a 2-step dance: `POST
/auth/coe-governance/request-code` emails a fresh 6-digit code to the
signed-in caller's own address (reusing the same OTP machinery as
sign-in — `airi.auth.generate_code`/`hash_code`/`verify_code` — under a
different `purpose`, `coe_toggle`, so it can never be satisfied by, or
collide with, an ordinary sign-in code — see `otp_codes.purpose`,
`sql/011`), then the caller submits that code alongside `{"enabled":
bool, "code": str}` to the toggle endpoint itself, which verifies it
before applying anything. Both endpoints are also admin-only, same
bucket as renaming a workspace or a project — a team member never sees
either toggle. The frontend's `governanceToggleRowHtml`/
`wireGovernanceToggle` (in `frontend/workspaces.html`) implement this
same request→reveal-code-row→confirm flow for both switches, so there's
one flow to reason about rather than two.

Deliberately **not** decided yet: any rule about *when* a flip should
be allowed — e.g. whether turning a project's governance off should be
blocked while it has open Mandatory gates. For now, once the code
verifies, the flip is unconditional either direction. Turning either
switch off doesn't delete anything already recorded on a project (risk
tier, gate state, ledger) — it just stops surfacing/accepting new
writes for it, so turning it back on picks up exactly where it left
off.

### Risk tiering: worst-factor-wins

Once governance is on for a project, it shows nothing but a 4-question
risk quiz (`RISK_FACTORS` in `airi/workspaces.py`: Data, Autonomy,
Exposure, Reversibility) until it's answered — answering it immediately
unlocks everything else. `compute_risk_tier` takes the
**max** score across the 4 answers, not an average: one severely-
scored factor is enough to make the whole project High, the same way a
single failed safety check outweighs three passing ones. That also
makes the tier self-explaining — the stored `risk_explanation` always
names the one factor that caused it (e.g. *"High, because of
autonomy: acts on its own (sends, changes, spends)."*), so a tier is
never a bare label. The quiz can be retaken any time; each answer
overwrites the last (full-replace, like every other settings field in
this codebase) and appends one `risk_set` ledger event.

### Roles: 3, not a 7-column RACI

`ACCOUNTABLE_ROLES` — Business Owner, Technical Owner, Governance
Owner — each accountable for a different question (does this exist /
does it work / does it meet CoE standards). `resolve_coe_roles`
defaults every unassigned role to the workspace admin, so a solo
workspace needs no role setup at all; an admin can reassign any role
to a specific member from the Governance tab, full-replace like the
risk answers.

### The 6 gates and 3 enforcement levels

`COE_GATES` — Frame, Design, Verify, Release, Run, Evolve-or-retire —
each with a guide question and one accountable role. A gate's status
(`GATE_STATUSES`: not started / in progress / cleared / flagged) is
independent of the others; there's no forced ordering. What *does*
depend on the risk tier is how much a gate's status matters —
`ENFORCEMENT_LOOKUP[tier][gate_key]` gives one of three levels:

- **advisory** — every gate at Low tier, and most gates at Standard.
  Nothing blocks; Guide/Ledger still run.
- **required_justification** — flagging (not clearing) requires a
  non-empty note (`validate_gate_update` enforces this at every tier,
  every level — it's the one Ledger field that's never optional).
- **mandatory** — Design/Verify/Release at High tier. Clearing one of
  these requires more than a note: the caller's `user_id` must match
  whoever `resolve_coe_roles` names for that gate's `accountable_role`,
  or the API 403s with a message naming which role can clear it
  ("Only the Technical Owner can clear this gate — reassign the role
  in the Governance tab if that's changed."). This is the one place
  identity is actually checked — every other gate action (opening,
  changing status, flagging, and clearing at lower enforcement levels)
  stays open to any active workspace member.

### Ledger: append-only, one row per write regardless of outcome

`coe_control_events` records every risk-answer submission and every
gate status change — including "I looked at this and left it
unchanged," so nothing is silently lost. `GET
/projects/{id}/coe-ledger` returns it newest-first; the frontend
renders it as a collapsed "History" section, reusing the
`.history-entry` pattern already used elsewhere in this file.

### Live AI Guide per gate

Each gate's static `guide_question` ("Does this meet the model,
architecture, and data standards?") is the same for every project.
`POST /projects/{id}/coe-phases/{gate_key}/ai-guide` turns it into a
short, concrete checklist tailored to *this* project — its title,
description, tech stack, risk tier, and this gate's enforcement level
at that tier — via a real generation call (`airi/ai_guide_provider.py`)
to Anthropic's `/v1/messages` or Google's `:generateContent`.

Three things distinguish this from every other CoE write:

- **Always BYOK, never AIRI's own key.** Unlike Exact mode
  (`airi/exact_provider.py`), which uses AIRI's shared server-held key
  in "test mode," this is a real, billed generation call — there's no
  free tier to fall back to, and no deployment-wide switch that changes
  that. `_resolve_ai_guide_api_key` in api.py has no test_mode branch
  at all; the request body just needs an `anthropic_api_key` or
  `google_api_key`, same optional fields as `ExactAnalyzeRequest`,
  400ing if neither is present.
- **Genuinely optional.** Every other part of the Governance tab — risk
  tiering, the 6 gates, roles, the ledger — works identically whether
  or not the caller has a BYOK key configured. This is a pure add-on
  layered on top: the frontend shows a quiet "add your own key"
  nudge (linking to the same "Manage keys" panel on `app.html` the
  Exact tab already uses) when neither `airi_byok_anthropic_key` nor
  `airi_byok_google_key` is in `localStorage`, and a "Get AI guide" /
  "Regenerate" button when one is.
- **The BYOK key itself is already global, not scoped to a workspace or
  project.** It lives in the browser's `localStorage`, shared across
  the whole app (`app.html`'s Exact tab and every project's Exact tab
  in `workspaces.html` already read the exact same two keys) — this
  feature reuses that as-is rather than adding a workspace-level or
  project-level key of its own.

The result is saved into `projects.coe_gate_ai_guides` (sql/012), keyed
by `gate_key`, and shown as-is until the caller clicks "Regenerate" —
there's no auto-refresh, matching the "save it, with a manual refresh
button" behavior decided for this feature. The write itself is an
atomic JSONB merge rather than the read-modify-write every other CoE
setter uses (see `set_project_gate_ai_guide` in `airi/db.py`): two
admins regenerating different gates' guides at close to the same time
is a real scenario this feature introduces, and only the merge shape
guarantees neither write clobbers the other. Generating a guide also
appends an `ai_guide_generated` ledger event (`gate_key`, and which
provider answered) — visibility into when and how a guide was produced,
without duplicating the (potentially large) checklist text into the
ledger itself.

Its own rate limit (`AI_GUIDE_CALLS_PER_MINUTE`, a separate in-process
sliding-window log from `EXACT_CALLS_PER_MINUTE`'s) exists purely to
absorb accidental repeated clicks against AIRI's own server — never to
protect a shared provider key, since there isn't one here. This is
deliberately its own log, not `_check_exact_rate_limit` reused, for the
exact reason the CoE-toggle 429s happened twice in a row (see "The two
switches" above): two unrelated features sharing one counter means a
burst of one silently eats into the other's budget.

### API reference (Phase 6)

| Endpoint | Purpose |
|---|---|
| `POST /workspaces` | Takes `coe_governance_enabled` (bool, default `false`) straight from the body, no step-up code — sets the default new projects in this workspace start with. |
| `PUT /workspaces/{id}` | Title/target/description only, admin-only. Does **not** touch `coe_governance_enabled` any more. |
| `PUT /workspaces/{id}/coe-governance` | Admin-only + a valid step-up code (see `POST /auth/coe-governance/request-code` below). Body: `{"enabled": bool, "code": str}`. Only changes what a *new* project in this workspace defaults to. |
| `POST /workspaces/{id}/projects` | Resolves the new project's `coe_governance_enabled` from the workspace's *current* switch at this exact moment — not part of the request body. |
| `PUT /projects/{id}/coe-governance` | Admin-only + a valid step-up code, same shape as the workspace one. This is the switch that actually governs the project — appends a `governance_toggled` ledger event either way. |
| `POST /auth/coe-governance/request-code` | Any signed-in user. Emails a fresh 6-digit code to the caller's own address (purpose `coe_toggle`), to be submitted to one of the two `coe-governance` endpoints above. Not scoped to a workspace/project — it only proves "signed in as this email, right now"; the confirm endpoints check admin permission on the specific resource separately. Its own independent per-email budget, keyed by `purpose` (never shared with sign-in codes) — and deliberately more generous than `POST /auth/request-code`'s (15s cooldown, 30/day vs. 60s/5 — see `COE_TOGGLE_REQUEST_COOLDOWN_SECONDS`/`COE_TOGGLE_DAILY_REQUEST_LIMIT` in api.py), since this endpoint always emails the already-signed-in caller's own address rather than an arbitrary one from the request body, so it isn't the same third-party-spam vector the login endpoint's strict limit exists to stop. |
| `GET /projects/coe-catalog` | Unauthenticated. Returns `RISK_FACTORS`, `COE_GATES`, `ACCOUNTABLE_ROLES`, `ENFORCEMENT_LOOKUP` so the frontend never hardcodes them. |
| `PUT /projects/{id}/coe-risk` | `400`s via `_require_coe_governance_enabled` if *this project's own* switch is off. Otherwise, body: one answer per `RISK_FACTORS` key — computes and stores `risk_tier`/`risk_explanation`, appends a `risk_set` ledger event. |
| `PUT /projects/{id}/coe-roles` | Admin-only, and 400s if this project's switch is off. Body: `{role_key: user_id or null}` for each `ACCOUNTABLE_ROLES` key. Full-replace. |
| `PUT /projects/{id}/coe-phases/{gate_key}` | 400s if this project's switch is off. Otherwise body: `{"status", "note"}`. `403`s if `status="cleared"` at Mandatory enforcement and the caller isn't the resolved accountable user for that gate; `400`s if `status="flagged"` with no note. Appends one `gate_status_changed` ledger event regardless of outcome. |
| `GET /projects/{id}/coe-ledger` | All `coe_control_events` for the project, newest-first. Not gated by the switch — reading a project's (possibly empty) history is harmless either way. |
| `POST /projects/{id}/coe-phases/{gate_key}/ai-guide` | Any active member; 400s if this project's switch is off, 404s for an unknown `gate_key`. Body: `{"anthropic_api_key"?, "google_api_key"?}` — always BYOK, no test_mode fallback; 400s if neither is present. Calls out to the real Anthropic/Google generation API (never a free/counting endpoint), saves the resulting checklist into `coe_gate_ai_guides` via an atomic JSONB merge, and appends an `ai_guide_generated` ledger event. Its own rate limit, `AI_GUIDE_CALLS_PER_MINUTE`, kept deliberately separate from `EXACT_CALLS_PER_MINUTE`. |

## What's next

No items from the original plan remain outstanding as of this
delivery.
