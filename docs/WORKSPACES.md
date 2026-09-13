# Workspaces & Projects (Phase 1)

`frontend/workspaces.html` lets a signed-in user organize their work
into **workspaces** (a team/initiative) containing **projects** (a
specific thing being analyzed), instead of using AIRI's tools as a
one-off calculator every time. This is **Phase 1 of a multi-phase
plan** — it ships the data model, the CRUD, and full restore-on-login,
but not yet the tabbed per-project tool integration. See "What's next"
at the bottom for exactly what's deferred and why.

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

## What's next (later phases — not in this delivery)

Per the plan agreed before building this: Phase 1 is the data model,
CRUD, and restore-on-login only. Still to come, in roughly this order:

1. **Per-project tool tabs** — each existing AIRI tool (Standard
   analyze, Exact mode, traffic projection, load-test report) embedded
   and runnable inside a project, with results saved to that project
   (a new `project_tool_runs` table).
2. **Dashboard tab** — charts/figures built from the saved tool runs
   above.
3. **Notes tab** — an append-only, timestamped comment history per
   project (read-only list, click an entry to preview it in full).
4. **Actions tab** — a consolidated findings report per project.
5. **Comparison tab** — appears once a workspace has 2+ projects,
   auto-updating in the background as tool runs are saved.
6. **PDF reports** — per-tab, consolidated-per-project, and
   comparison-level, each signed by the user who created the
   project/workspace (their name/email — not the site's Author/founder
   profile, which is a separate, unrelated page).
7. **Workspace-level reporting** and **real team collaboration**
   (a member actually signing in and seeing the workspace, rather than
   just being notified) are open design questions for a later phase.
