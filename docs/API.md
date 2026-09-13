# AIRI API reference

Base URL: your own deployment, or the public demo at
`https://airi-mvp-api.onrender.com`. **The demo host is for trying AIRI
out, not for routing another application's production traffic through**
— it's a free-tier instance with no auth, no rate limiting, and no
uptime guarantee, and it spins down when idle (see "Cold starts" below).
For real integration, self-host it: `pip install -r requirements.txt`
and run `uvicorn api:app`, or deploy your own copy with `render.yaml`
(see the main [README](../README.md#quick-start-running-locally)).

There is no authentication in this build — see the README's "What was
cut" section. Add a key check in `api.py` before exposing an instance
you don't control publicly.

All responses are JSON. All endpoints are stateless — nothing you send
is stored.

---

## `GET /health`

Liveness check.

**Response 200**
```json
{ "status": "ok" }
```

---

## `GET /models`

Every model in the registry, for populating a dropdown or validating a
model name client-side before calling `/analyze`.

**Response 200**
```json
[
  { "id": "gpt-4o", "context_window": 128000, "input_price_per_1m": 2.5, "output_price_per_1m": 10.0 },
  { "id": "claude-3-5-sonnet", "context_window": 200000, "input_price_per_1m": 3.0, "output_price_per_1m": 15.0 }
]
```

A model name *not* in this list still works with `/analyze` and
`/project` — it just comes back flagged `"known_model": false` and
`"confidence": "low"` instead of being rejected. See
`airi/registry.py` to add a model permanently.

---

## `POST /analyze`

Estimate tokens, context usage and cost for one request, before you
send it to a model.

**Request body**

| field | type | required | notes |
|---|---|---|---|
| `prompt` | string | one of `prompt`/`messages` | a plain-text prompt |
| `messages` | array of `{role, content}` | one of `prompt`/`messages` | chat-style history; use this OR `prompt`, not both |
| `model` | string | no (default `gpt-4o`) | see `GET /models`; unknown values still work, see above |
| `expected_output_tokens` | integer ≥ 0 | no (default `0`) | your own estimate of the response length — AIRI does not predict this for you |

Request size is capped at 200,000 characters combined across
`prompt`/`messages` (`MAX_PROMPT_CHARS` in `api.py`).

```json
{
  "prompt": "Explain quantum computing simply.",
  "model": "gpt-4o",
  "expected_output_tokens": 500
}
```

**Response 200**

```json
{
  "model": "gpt-4o",
  "input_tokens": 8,
  "estimated_output_tokens": 500,
  "estimated_total_tokens": 508,
  "context_window": 128000,
  "context_utilization": 0.004,
  "estimated_cost": 0.00502,
  "method": "tokenizer",
  "confidence": "high",
  "status": "SAFE",
  "known_model": true
}
```

| field | meaning |
|---|---|
| `input_tokens` | tokens in `prompt`/`messages` as sent |
| `estimated_output_tokens` | echoes your `expected_output_tokens` input |
| `estimated_total_tokens` | `input_tokens + estimated_output_tokens` |
| `context_window` | the model's context size from the registry (or a conservative default for an unknown model) |
| `context_utilization` | `estimated_total_tokens / context_window`, 0–1+ |
| `estimated_cost` | USD, from the registry's per-1M-token pricing |
| `method` | `"tokenizer"` (exact, via tiktoken) or `"heuristic"` (chars/4 estimate) — see the README's "Tokenizer accuracy" |
| `confidence` | `"high"` (exact tokenizer), `"medium"` (heuristic on a known model), or `"low"` (unrecognized model) |
| `status` | `"SAFE"` (<80% of context window), `"WARNING"` (≥80%), or `"EXCEEDED"` (over 100%) |
| `known_model` | whether `model` was found in the registry |

**Errors**

- `400` — `{"detail": "..."}` — both `prompt` and `messages` given, neither given, or input too large. AIRI's own validation message.
- `422` — pydantic request-shape errors (wrong types, missing fields, `expected_output_tokens` negative). FastAPI's standard validation error body.

---

## How `status` is decided (worked examples)

`status` comes from one number, `context_utilization`:

```
context_utilization = estimated_total_tokens / context_window
```

(`estimated_total_tokens` is `input_tokens + estimated_output_tokens` —
the request as sent, plus your own estimate of the response.)

| status | condition | meaning |
|---|---|---|
| `SAFE` | `context_utilization < 0.80` | comfortable headroom |
| `WARNING` | `0.80 ≤ context_utilization ≤ 1.00` | fits, but close — the next turn in a conversation, or a slightly longer response than expected, could tip it over |
| `EXCEEDED` | `estimated_total_tokens > context_window` (i.e. `context_utilization > 1.00`) | the provider **will** reject this; AIRI isn't guessing here, it's arithmetic |

Two things worth knowing precisely: the 80% cutoff is `>=`, so exactly
80.00% is already `WARNING`, not `SAFE`. And exactly 100% (the request
fits with zero tokens to spare) is still `WARNING`, not `EXCEEDED` —
`EXCEEDED` requires actually going *over* the window. Both thresholds
live in `airi/registry.py` (`WARNING_THRESHOLD`) and the comparison
itself in `airi/analyzer.py`, if you want to tune them for your own
risk tolerance.

The three worked examples below were captured against the live API
(`https://airi-mvp-api.onrender.com`) with real requests, so the numbers
are reproducible, not illustrative — the `gpt-4`/`gpt-4o` ones used the
exact tiktoken tokenizer (`confidence: "high"`). Inside `/project`,
the identical logic runs once per archetype and shows up as that
archetype's `unit.status`.

### Example: SAFE

A short prompt on a large-context model — plenty of headroom.

Request:
```json
{ "prompt": "Explain quantum computing simply.", "model": "gpt-4o", "expected_output_tokens": 500 }
```

Response:
```json
{
  "model": "gpt-4o",
  "input_tokens": 5,
  "estimated_output_tokens": 500,
  "estimated_total_tokens": 505,
  "context_window": 128000,
  "context_utilization": 0.0039,
  "estimated_cost": 0.005013,
  "method": "tokenizer",
  "confidence": "high",
  "status": "SAFE"
}
```

**Usage**: send it. Nothing more to do.

### Example: WARNING

A long input (~6,000 words) against `gpt-4`'s comparatively small
8,192-token window — no output expected, and it's already at 89%.

Request:
```json
{ "prompt": "<a ~6,000-word document>", "model": "gpt-4", "expected_output_tokens": 0 }
```

Response:
```json
{
  "model": "gpt-4",
  "input_tokens": 7285,
  "estimated_output_tokens": 0,
  "estimated_total_tokens": 7285,
  "context_window": 8192,
  "context_utilization": 0.8893,
  "estimated_cost": 0.21855,
  "method": "tokenizer",
  "confidence": "high",
  "status": "WARNING"
}
```

**Usage**: still safe to send as-is (there's no output budget here to
push it over), but if this were a multi-turn conversation, the *next*
message added to this same history would likely tip it into `EXCEEDED`.
Log it, and consider trimming history now rather than after the next
call fails.

### Example: EXCEEDED

The same kind of long input (~7,500 words), this time also budgeting
500 output tokens — together they overshoot `gpt-4`'s window by 17%.

Request:
```json
{ "prompt": "<a ~7,500-word document>", "model": "gpt-4", "expected_output_tokens": 500 }
```

Response:
```json
{
  "model": "gpt-4",
  "input_tokens": 9106,
  "estimated_output_tokens": 500,
  "estimated_total_tokens": 9606,
  "context_window": 8192,
  "context_utilization": 1.1726,
  "estimated_cost": 0.30318,
  "method": "tokenizer",
  "confidence": "high",
  "status": "EXCEEDED"
}
```

**Usage**: don't send this — the provider will reject it outright
(most return a 400-class error and you still get billed nothing, but
you've burned a round trip and, in a user-facing flow, their patience).
Trim the input (see the `fit_to_context` example in
[docs/INTEGRATION.md](INTEGRATION.md#option-a-python--import-the-library-directly)),
shorten `expected_output_tokens`, or move to a larger-context model —
then re-check before sending.

---

## `POST /project`

Project total tokens/cost across several distinct AI call-sites at
once, given a volume you supply for each — see the README's "Volume
projection" section for the concept. AIRI does not estimate volume
itself; it multiplies what you give it.

**Request body**

```json
{
  "archetypes": [
    {
      "name": "chat reply",
      "volume": 10000,
      "prompt": "Hi there, how can I help you today?",
      "model": "gpt-4o-mini",
      "expected_output_tokens": 80
    },
    {
      "name": "doc summary",
      "volume": 500,
      "prompt": "Summarize this document for the user in three bullet points.",
      "model": "claude-3-5-sonnet",
      "expected_output_tokens": 300
    }
  ]
}
```

Each entry in `archetypes` takes the same fields as `/analyze` (either
`prompt` or `messages`, `model`, `expected_output_tokens`) plus:

| field | type | required | notes |
|---|---|---|---|
| `name` | string, 1–200 chars | yes | a label for this call-site |
| `volume` | integer ≥ 0 | yes | however many requests you expect, in whatever period you're planning for |

1–50 archetypes per request (`MAX_ARCHETYPES` in `airi/projector.py`).

**Response 200**

```json
{
  "archetypes": [
    {
      "name": "chat reply",
      "model": "gpt-4o-mini",
      "volume": 10000,
      "unit": { "...": "the full /analyze-shaped result for one request of this type" },
      "projected_input_tokens": 90000,
      "projected_output_tokens": 800000,
      "projected_total_tokens": 890000,
      "projected_cost": 0.49
    }
  ],
  "total_volume": 10500,
  "total_tokens": 1047500,
  "total_cost": 2.7625,
  "cost_by_model": { "gpt-4o-mini": 0.49, "claude-3-5-sonnet": 2.2725 },
  "tokens_by_model": { "gpt-4o-mini": 890000, "claude-3-5-sonnet": 157500 },
  "any_exceeded": false
}
```

`any_exceeded` is `true` if any single archetype's *unit* request
(before multiplying by volume) already exceeds its model's context
window on its own — a signal to fix that request shape regardless of
volume, not something scaled by volume.

**Errors**: same shapes as `/analyze` — `400` for AIRI's own validation
(empty list, too many archetypes, negative volume, a bad `prompt`/
`messages` combination inside one archetype), `422` for request-shape
errors.

---

## `POST /report`, `POST /report/html`, `POST /report/pdf`

Consolidate a load-test run's per-request `/analyze` results into one
report — see the README's "Load-test token usage reporting" for the
concept and end-to-end flow. All three endpoints take the identical
request body and differ only in response format. Fully stateless:
nothing you submit is stored; submit everything your test harness
collected in one call, get one report back.

**Request body**

```json
{
  "run_name": "Nightly load test — checkout flow",
  "records": [
    {
      "model": "gpt-4o-mini",
      "input_tokens": 42,
      "estimated_output_tokens": 120,
      "estimated_total_tokens": 162,
      "context_window": 128000,
      "context_utilization": 0.0013,
      "estimated_cost": 0.0000957,
      "method": "tokenizer",
      "confidence": "high",
      "status": "SAFE",
      "known_model": true,
      "label": "checkout-summary",
      "phase": "normal",
      "timestamp": "2026-09-13T10:00:05Z"
    }
  ]
}
```

| field | type | required | notes |
|---|---|---|---|
| `run_name` | string, 1–200 chars | yes | a label for this run |
| `records` | array, 1–20,000 entries | yes | one entry per AI request made during the run |

Each entry in `records` is exactly the shape `/analyze` returns
(`model`, `input_tokens`, `estimated_output_tokens`,
`estimated_total_tokens`, `context_window`, `context_utilization`,
`estimated_cost`, `method`, `confidence`, `status`, `known_model`) —
literally what your harness already has from calling `/analyze` during
the run — plus:

| field | type | required | notes |
|---|---|---|---|
| `label` | string | yes | which service/call-site this request was |
| `phase` | string | no | a freeform tag, e.g. `"normal"` or `"peak"`; omitted entries are grouped under `"unspecified"` |
| `timestamp` | string (ISO 8601) | no | when the request happened — supply it on at least 2 records to get `duration_seconds`/`requests_per_second` in the report |

`records` accepts extra fields beyond these too (they're ignored) — you
can pass your raw `/analyze` response objects straight through after
adding `label`/`phase`/`timestamp`, no need to strip anything.

### `POST /report` — JSON summary

**Response 200**

```json
{
  "run_name": "Nightly load test — checkout flow",
  "generated_at": "2026-09-13T15:39:20.415774+00:00",
  "total_requests": 16,
  "total_input_tokens": 9412,
  "total_output_tokens": 1580,
  "total_tokens": 26733,
  "total_cost": 0.78617,
  "avg_tokens_per_request": 1670.8,
  "avg_cost_per_request": 0.049136,
  "peak_request": { "...": "the full record with the highest estimated_total_tokens" },
  "status_counts": { "SAFE": 15, "WARNING": 0, "EXCEEDED": 1 },
  "by_model": { "gpt-4o-mini": { "count": 8, "tokens": 1600, "cost": 0.012 } },
  "by_label": { "checkout-summary": { "count": 8, "tokens": 1600, "cost": 0.012, "SAFE": 8, "WARNING": 0, "EXCEEDED": 0 } },
  "by_phase": { "normal": { "count": 10, "tokens": 2000, "cost": 0.015 }, "peak": { "count": 6, "tokens": 24733, "cost": 0.771 } },
  "flagged_requests": [ "...records with status WARNING or EXCEEDED, worst first, capped at 50" ],
  "flagged_truncated": false,
  "duration_seconds": 16.0,
  "requests_per_second": 1.0
}
```

`peak_request` and `flagged_requests` entries carry the full record you
submitted (including `label`/`phase`/`timestamp`). `duration_seconds`/
`requests_per_second` are `null` when fewer than 2 records have a
parseable `timestamp`.

### `POST /report/html` — rendered HTML page

Same data, returned as `text/html`: a single self-contained page (no
external CSS/JS/fonts) suitable for embedding in an `<iframe>` or
opening directly in a browser.

### `POST /report/pdf` — downloadable PDF

Same page, converted to `application/pdf` via `xhtml2pdf` and returned
with `Content-Disposition: attachment; filename="<run_name>.pdf"` —
open it directly or save it. The HTML and PDF come from the identical
template (`airi/report_render.py`), so they always agree.

**Errors** (all three endpoints)

- `400` — `{"detail": "..."}` — empty `records`, more than 20,000
  records, a record missing a required field (named in the message), or
  an unrecognized `status` value. AIRI's own validation.
- `422` — pydantic request-shape errors (`run_name` blank/too long,
  `records` not a list of objects).
- `500` (`/report/pdf` only) — PDF rendering failed unexpectedly.

## "Exact" flavor: auth + `/analyze/exact`

See [docs/EXACT_MODE.md](EXACT_MODE.md) for the concept, the zero-cost
design, and one-time setup (Neon/Resend/Anthropic/Google). This section
is just the request/response reference for the four endpoints involved.
All four return a `503` with a plain-language `detail` if their required
env vars aren't set on this deployment — none of this affects `/analyze`,
`/project`, or `/report*`, which have no auth and never did.

### `POST /auth/request-code`

**Request**: `{"email": "you@example.com"}`

**Response 200**: `{"message": "Check your email for a 6-digit code. It expires in 10 minutes."}`
— always this generic message, whether or not the email has signed in before.

**Errors**: `400` invalid email; `429` cooldown (max 1 per 60s) or daily
cap (max 5/24h) hit for this email; `502` the email couldn't be sent;
`503` not configured on this deployment.

### `POST /auth/verify-code`

**Request**: `{"email": "you@example.com", "code": "042817"}`

**Response 200**: `{"token": "<jwt>", "email": "you@example.com"}` — send
`token` back as `Authorization: Bearer <token>` on the two endpoints below.

**Errors**: `400` no code requested / wrong code / too many attempts (5
max) / code expired (10 min); `503` not configured.

### `GET /auth/me`

**Header**: `Authorization: Bearer <token>`

**Response 200**: `{"email": "you@example.com"}` — lets a client check a
stored token is still valid without repeating the OTP flow.

**Errors**: `401` missing/invalid/expired token; `503` not configured.

### `POST /analyze/exact`

Same request body as [`POST /analyze`](#post-analyze) (`prompt` or
`messages`, `model`, `expected_output_tokens`), same response shape —
plus `Authorization: Bearer <token>` required, two extra optional
request fields, and one extra optional response field:

| field | meaning |
|---|---|
| `anthropic_api_key` (request, optional) | your own Anthropic key — used only in BYOK mode (see below); ignored in test mode; never stored |
| `google_api_key` (request, optional) | your own Google (Gemini) key — same |
| `exact_mode_note` (response, optional) | present only if the real provider call failed (rate limit, outage, bad BYOK key) — the response still has valid data, just from the heuristic fallback, and this field says so honestly instead of silently mislabeling it |

For OpenAI models this endpoint returns exactly what `/analyze` would
(tiktoken is already exact and free — no provider call needed). For
Claude/Gemini models, `method` is `"provider-api"` and `confidence` is
`"high"` when the provider call succeeds.

**Which key is used** depends on this deployment's test-mode setting
(`GET /config` → `test_mode`; full detail in
[docs/ADMIN.md](ADMIN.md)):

- **Test mode on:** AIRI's own `ANTHROPIC_API_KEY`/`GOOGLE_API_KEY`.
  `anthropic_api_key`/`google_api_key` in the request are ignored.
- **Test mode off (BYOK):** the request's own `anthropic_api_key` /
  `google_api_key`, matching the model's provider. Missing the one you
  need is a `400`, not a `503` — it's a per-caller, fixable problem.

**Errors**: `400`/`422` same as `/analyze`, plus (BYOK mode only) `400`
if the request is missing the API key for that model's provider; `401`
not signed in / session expired; `429` rate-limited (max 20
calls/minute per signed-in user); `503` (test mode only) Exact mode for
that provider isn't configured on this deployment.

## Admin: test mode, BYOK, and deployment config

See [docs/ADMIN.md](ADMIN.md) for the concept and the password-gated
admin page (`frontend/admin.html`). Endpoint summary:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /config` | none | `{"test_mode": bool}` — what the Exact-flavor frontend needs to decide what to show a signed-in user |
| `POST /admin/login` | `{"password": "..."}` in body | Returns a 12h admin token on success (`401` wrong password, `503` not configured) |
| `GET /admin/config` | `Authorization: Bearer <admin token>` | Current `test_mode` + its `source` (`"admin"`/`"env"`/`"default"`), plus a read-only checklist of which secrets are configured |
| `POST /admin/config` | same | Body `{"test_mode": bool}` — sets an admin override (persists in Postgres); `503` if the database isn't configured |
| `GET /author` | none | The founder/author profile shown on `frontend/author.html` — all-empty strings if nothing's been set |
| `POST /admin/author` | `Authorization: Bearer <admin token>` | Body is the full profile (see [docs/ADMIN.md](ADMIN.md#author-profile)) — a full replace; `400` on an invalid field (nothing is written), `503` if the database isn't configured |

An admin token is a distinct, shorter-lived (12h) JWT from a user's
Exact-mode session token — `airi/auth.py` rejects either type outright
if presented as the other, even though both are HS256-signed with the
same `AUTH_SECRET`.

## Workspaces & projects (Phases 1–4)

See [docs/WORKSPACES.md](WORKSPACES.md) for the full concept, schema,
and design rationale (the app-key delete-confirmation PIN, tech-stack
categories). All endpoints below require
`Authorization: Bearer <session token>` (from `POST /auth/verify-code`
— the same sign-in `/analyze/exact` uses) and `401` without one. A
workspace/project id belonging to another user returns `404`, same as
one that doesn't exist.

| Endpoint | Purpose |
|---|---|
| `GET /profile` | `{"has_app_key": bool}` — never the key or its hash |
| `POST /profile/app-key` | Body `{"app_key": "1234"}` (exactly 4 digits) — sets/changes it; `400` if malformed |
| `GET /workspaces` | Every workspace this user owns, with `member_count`/`project_count` |
| `POST /workspaces` | Body `{"title", "target", "description"}` (`title` required) |
| `GET /workspaces/{id}` | Full detail incl. `members` and `projects` arrays — one call to restore everything on login |
| `PUT /workspaces/{id}` | Same body as create — full replace of the three fields |
| `DELETE /workspaces/{id}` | Body `{"app_key": "1234"}` — cascades to members/projects; `400` if the key is wrong or never set |
| `POST /workspaces/{id}/members` | Body `{"email": "..."}` — `409` if already a member; sends a notification email (best-effort) |
| `DELETE /workspaces/{id}/members/{member_id}` | Sends a removal notification email the same way |
| `GET /projects/tech-stack-categories` | Public — the tech-stack category table (label + required/optional) |
| `POST /workspaces/{id}/projects` | Body `{"title", "description", "tech_stack": {...}}` — `400` if `title` is blank or `tech_stack` is missing `ai_services`/`ai_model` |
| `GET /projects/{id}` / `PUT /projects/{id}` | Same shape as create |
| `DELETE /projects/{id}` | Body `{"app_key": "1234"}`, same gate as deleting a workspace |
| `POST /projects/{id}/tools/analyze/runs` | Same body as `POST /analyze` + optional `label` — runs it and saves the result |
| `POST /projects/{id}/tools/exact/runs` | Same body as `POST /analyze/exact` + optional `label` — a BYOK key in the body is used for the call but never persisted |
| `POST /projects/{id}/tools/project/runs` | Same body as `POST /project` + optional `label` |
| `POST /projects/{id}/tools/report/runs` | Same body as `POST /report` + optional `label` |
| `GET /projects/{id}/tools/{tool}/runs` | That tool's saved runs, most recent first (`tool`: `analyze`/`exact`/`project`/`report`, else `422`) |
| `DELETE /projects/{id}/tools/{tool}/runs/{run_id}` | Body `{"app_key": "1234"}`, same delete gate |
| `POST /projects/{id}/notes` | Body `{"body": "..."}` (1–5000 chars after trimming) — creates one note; `400` if blank/too long |
| `GET /projects/{id}/notes` | This project's notes, most recent first |
| `DELETE /projects/{id}/notes/{note_id}` | Body `{"app_key": "1234"}`, same delete gate |
| `GET /projects/{id}/report/consolidated` | Aggregated totals/by-tool/latest-run/notes across the whole project, as JSON — backs the Dashboard and Actions tabs |
| `GET /projects/{id}/report/consolidated.pdf` | The same aggregation, rendered to a downloadable PDF, signed with the session's own email |
| `GET /projects/{id}/tools/{tool}/runs/{run_id}/pdf` | One saved run, rendered to a downloadable PDF; `404` if the run doesn't belong to that project or `{tool}` doesn't match its actual tool |
| `GET /workspaces/{id}/comparison` | Cross-project comparison as JSON: `workspace`, `prepared_by`, `generated_at`, `workspace_totals`, `projects` (ranked most-expensive-first) |
| `GET /workspaces/{id}/comparison.pdf` | The same comparison, rendered to a downloadable PDF |

See [docs/WORKSPACES.md](WORKSPACES.md#tool-runs-phase-2-airis-tools-inside-a-project)
for what gets stored, and why a BYOK key never does,
[docs/WORKSPACES.md](WORKSPACES.md#dashboard-notes--actions-phase-3)
for the Phase 3 tabs (Dashboard/Notes/Actions) and the consolidated
report, and
[docs/WORKSPACES.md](WORKSPACES.md#cross-project-comparison--per-run-pdfs-phase-4)
for the Phase 4 comparison tab and per-run PDF downloads.

## `GET /download`

Unauthenticated. Returns a zip (`airi-binary.zip`) containing a
**compiled build of just AIRI's core estimation library** — the
`airi/` files with zero web/db/cloud dependencies (`__init__.py`,
`models.py`, `registry.py`, `pricing.py`, `tokenizer.py`,
`analyzer.py`, `projector.py`, `report.py`, `report_render.py`; see
README.md's project layout), compiled to `.pyc` bytecode with no `.py`
source included. AIRI's source is not publicly distributed — this
replaces an earlier version of this endpoint that shipped the entire
repository (library, API, frontend, SQL migrations, docs); that is no
longer offered. The file set is a hardcoded allowlist in `api.py`
(`_CORE_LIBRARY_FILES`), not a denylist over the whole tree, so
nothing outside it can be shipped by accident.

Because it's compiled bytecode, it's tied to the exact CPython minor
version running on this deployment (currently 3.11.x — see
`PY_BINARY_VERSION` in `api.py`, and the bundled `README.txt` for the
exact version at download time); importing it under a different Python
3 minor version raises `ImportError: bad magic number`. The zip is
rebuilt at most once every 5 minutes and served from an in-memory
cache in between (`DOWNLOAD_CACHE_SECONDS`), so a code change to the
core library is reflected here automatically on the next rebuild —
no separate publish step.

## Cold starts (demo host only)

If you're hitting `https://airi-mvp-api.onrender.com`, Render's free
tier spins the service down after a period of inactivity. The first
request after that can take 30–60 seconds while it wakes up — this
is Render's behavior, not an AIRI bug. Set a generous timeout (60s+)
and consider a warm-up request if you're demoing it live. A self-hosted
instance on a paid plan, or any always-on host, doesn't have this
issue.
