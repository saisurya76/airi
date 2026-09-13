# Workspaces & Projects (Phases 1–4)

`frontend/workspaces.html` lets a signed-in user organize their work
into **workspaces** (a team/initiative) containing **projects** (a
specific thing being analyzed), instead of using AIRI's tools as a
one-off calculator every time. This is a **multi-phase plan**: Phase 1
shipped the data model, CRUD, and full restore-on-login; Phase 2 made
AIRI's tools runnable and saved inside a project; Phase 3 added a
Dashboard tab, a Notes tab, and an Actions tab with a downloadable
consolidated PDF report; Phase 4 (this delivery) adds a cross-project
comparison tab with its own PDF report, plus per-saved-run PDF
downloads. See "What's next" at the bottom for what's still deferred
and why.

Like Exact mode, this entire feature requires a signed-in session
(email + OTP — see [docs/EXACT_MODE.md](EXACT_MODE.md)). It doesn't
touch `/analyze`, `/project`, `/report*`, which remain completely
anonymous and stateless.

## Data model

Three new tables (`sql/003_workspaces_schema.sql`), on top of the
`users` table Exact mode already created:

- **`user_profile`** — one row per user, holding only a hash of their
  4-digit "app key" (see below). Created lazily on first save, not at
  signup.
- **`workspaces`** — owned by exactly one user (`owner_user_id`).
  `title` (required), `target` (the high-level target of the exercise),
  `description`.
- **`workspace_members`** — email addresses invited into a workspace.
  Phase 1 treats this as informational: adding/removing a member sends
  them a standard notification email, but a member doesn't get their
  own access to the workspace yet. Real team collaboration (a member
  signing in and seeing/editing the workspace themselves) is flagged
  below as likely Phase 2, once this phase is confirmed working.
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
(from `POST /auth/verify-code`) and return `401` without one. A
workspace/project id that exists but belongs to another user returns
`404`, identically to one that doesn't exist at all — this deliberately
never confirms another user's workspace/project id is valid.

### Profile

- `GET /profile` → `{"has_app_key": bool}`.
- `POST /profile/app-key` — body `{"app_key": "1234"}` (exactly 4
  digits). Sets or overwrites the key. `400` if not 4 digits.

### Workspaces

- `GET /workspaces` — every workspace this user owns, with
  `member_count`/`project_count`, most recent first.
- `POST /workspaces` — body `{"title", "target", "description"}`
  (`title` required). Returns the new workspace with empty
  `members`/`projects` arrays.
- `GET /workspaces/{id}` — full detail: the workspace fields plus
  `members` and `projects` arrays — everything needed to restore the
  page on login in one call.
- `PUT /workspaces/{id}` — same body shape as create; a full replace
  of the three editable fields.
- `DELETE /workspaces/{id}` — body `{"app_key": "1234"}`. Cascades to
  members and projects. `400` if the app key is missing/wrong/unset.

### Team members

- `POST /workspaces/{id}/members` — body `{"email": "..."}`. `409` if
  already a member (case-insensitive). Sends `send_member_added_email`
  (`airi/email_provider.py`) best-effort — a Resend hiccup doesn't fail
  the request, since the membership itself is what matters.
- `DELETE /workspaces/{id}/members/{member_id}` — sends
  `send_member_removed_email` the same way.

### Projects

- `GET /projects/tech-stack-categories` — public (no auth needed): the
  category table above, as JSON.
- `POST /workspaces/{id}/projects` — body `{"title", "description",
  "tech_stack": {...}}`. `400` if `title` is blank or `tech_stack` is
  missing `ai_services`/`ai_model`.
- `GET /projects/{id}` / `PUT /projects/{id}` — same shape.
- `DELETE /projects/{id}` — body `{"app_key": "1234"}`, same gate as
  deleting a workspace.

## Tool runs (Phase 2): AIRI's tools, inside a project

Each of AIRI's four existing tools is now runnable *and saved* inside a
project, in a tab of its own on `frontend/workspaces.html`: **Standard**
(`/analyze`), **Exact** (`/analyze/exact`), **Traffic** (`/project`,
volume projection), and **Load Test** (`/report`, load-test reporting).

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

**"Signed by the user who initiates/creates the project"**: today, the
only user who can ever reach a project *is* the workspace owner — real
team collaboration (a member getting their own access, not just an
email notification) hasn't shipped yet — so the signed-in session's own
email is, by construction, the project's creator. The PDF's "Prepared
by" line uses that email directly; there's no separate name field to
pull from (the `users` table only ever stores an email — see
`sql/001_auth_schema.sql`). This will need a real per-project creator
lookup once team collaboration ships and a project can be opened by
someone other than the person who created it.

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
line uses the signed-in session's own email, for the same reason as
the consolidated report (see above) — real team collaboration hasn't
shipped, so the workspace owner is, by construction, the only person
who can ever generate it.

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

## What's next (later phases — not in this delivery)

Per the plan agreed before building this: Phase 1 shipped the data
model, CRUD, and restore-on-login; Phase 2 made AIRI's tools runnable
and saved inside a project; Phase 3 added the Dashboard, Notes, and
Actions tabs; Phase 4 (above) added the cross-project comparison tab,
its PDF report, and per-run PDF downloads. Still to come:

1. **Real team collaboration** — a member actually signing in and
   seeing the workspace themselves (rather than just being notified by
   email when added or removed) remains an open design question for a
   later phase. This is also what the "prepared by" simplification
   used throughout the consolidated and comparison reports (see above)
   is waiting on: today it's always the workspace owner's own email,
   because real team collaboration hasn't shipped and no one else can
   reach a project or workspace yet.

No other items from the original 4-phase plan remain outstanding.
