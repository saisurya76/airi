# AIRI — AI Request Intelligence (lean MVP)

A small library + API + try-it page that estimates tokens, context usage
and cost for an AI request *before* you send it, so an app can decide to
SEND / MODIFY / REJECT. Not a token counter — a pre-flight check.

This is a deliberately cut-down build of the frozen spec (`AIRI MVP
Requirements Freeze v0.1`), scoped to exactly what you asked for: a
token estimation library, one API endpoint, and a frontend page to try
it. See **What was cut** below for what that spec included that this
build intentionally skips.

## Documentation

- **[docs/INTEGRATION.md](docs/INTEGRATION.md)** — wiring AIRI into your
  own AI request pipeline as a pre-flight SEND/MODIFY/REJECT check,
  from Python or any other language over HTTP.
- **[docs/API.md](docs/API.md)** — full reference for every endpoint
  (`/analyze`, `/project`, `/report`, `/models`, `/health`, `/auth/*`,
  `/analyze/exact`): request/response schemas and error formats.
- **[docs/EXACT_MODE.md](docs/EXACT_MODE.md)** — the opt-in "Exact"
  flavor (real Claude/Gemini token counts via email+OTP sign-in): why
  it's gated, why it's zero-cost even at peak traffic, one-time setup
  (Neon, Resend, Anthropic/Google keys), and the account-wide Terms &
  Conditions acceptance gate shared with Workspaces.
- **[docs/ADMIN.md](docs/ADMIN.md)** — the password-gated admin page
  (`frontend/admin.html`): the test-mode ↔ BYOK toggle for Exact mode,
  the founder/author profile shown on `frontend/author.html`, and the
  `/config`/`/admin/*`/`/author` API reference.
- **[docs/WORKSPACES.md](docs/WORKSPACES.md)** — workspaces/projects
  (`frontend/workspaces.html`, Phases 1–5): the app-key delete-confirmation
  PIN, tech-stack categories, running AIRI's tools inside a project with
  saved history, the per-project Dashboard/Notes/Actions tabs and
  downloadable consolidated PDF report, the cross-project comparison tab
  with its own PDF report, per-run PDF downloads, real team-member
  access with admin/member roles and an access-code sign-in, and the
  full `/profile`/`/workspaces`/`/projects`/`/auth/member-login` API
  reference.
- This README covers setup, the API contract at a glance, tokenizer
  accuracy, and what was cut from the frozen spec and why.

## Live

- Try-it page: hosted on GitHub Pages from `frontend/` via the workflow in
  `.github/workflows/pages.yml` — deploys automatically on every push to
  `main` that touches `frontend/`.
- API: hosted on Render (`render.yaml` at the repo root defines the
  service) at `https://airi-mvp-api.onrender.com`. Render's free tier
  spins the service down when idle, so the first request after a quiet
  period can take 30–60s to wake it up — that's expected, not a bug.
- If you redeploy the API under a different Render service name, update
  the `API_BASE` constant near the top of `frontend/index.html`'s
  `<script>` block and push — Pages picks it up automatically.

GitHub Pages only serves static files, so the API can't live there too —
that's why it's split across two hosts. `render.yaml` lets Render deploy
straight from this repo with no extra setup beyond connecting the repo
in the Render dashboard.

## Quick start (running locally)

```bash
pip install -r requirements.txt
uvicorn api:app --reload
```

Open `http://127.0.0.1:8000/` for the try-it page, or call the API directly:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain quantum computing simply.", "model": "gpt-4o", "expected_output_tokens": 500}'
```

Use the core library directly with no server at all:

```python
from airi import analyze

result = analyze(prompt="Explain quantum computing simply.", model="gpt-4o", expected_output_tokens=500)
print(result.to_dict())
```

Run the sanity tests any time with `python3 tests/test_analyzer.py`,
`python3 tests/test_projector.py`, `python3 tests/test_report.py`,
`python3 tests/test_auth.py`, `python3 tests/test_exact_provider.py`,
`python3 tests/test_email_provider.py`, `python3 tests/test_runtime_config.py`,
`python3 tests/test_author.py`, `python3 tests/test_workspaces.py`,
`python3 tests/test_tool_runs.py`, `python3 tests/test_notes.py`,
`python3 tests/test_consolidated_report.py`, and
`python3 tests/test_comparison.py`
(none of these need a database/network — they test pure logic and
mocked HTTP calls), or all at once with `pytest tests/ -q`.

## Project layout

```
airi/                   core library — zero web/db/cloud dependencies
  analyzer.py             analyze() — per-request estimate
  projector.py             project() — multi-archetype volume projection, built on analyze()
  report.py                 build_report() — consolidate a load-test run's analyze() results
  report_render.py            render_report_html() — one HTML template, also fed to xhtml2pdf
  tokenizer.py              exact counts (tiktoken) with heuristic fallback
  pricing.py                 cost = tokens x registry price
  registry.py                  model -> context window, price, tokenizer family, provider
  models.py                     AnalysisResult
  auth.py                 API-layer only: OTP code hashing + JWT sessions (pure, no I/O), incl. admin tokens + team-member access-code generation/verification
  db.py                   API-layer only: Neon/Postgres access for users + otp_codes + app_config
  email_provider.py       API-layer only: sends the OTP email via Resend
  exact_provider.py       API-layer only: real Anthropic/Google token-counting API calls
  runtime_config.py       API-layer only: test-mode/BYOK toggle (env var + admin-page override)
  author.py               API-layer only: founder/author profile storage + validation (app_config-backed)
  workspaces.py           API-layer only: app-key PIN hashing + workspace/project/tech-stack validation (pure logic)
  tool_runs.py            API-layer only: ToolName enum + run-label validation for saved per-project tool runs (pure logic)
  notes.py                API-layer only: note-body validation for a project's comment history (pure logic)
  consolidated_report.py  API-layer only: rolls up a project's saved tool runs + notes into one report, plus a single-run PDF template; HTML/PDF templates included (pure logic)
  comparison.py           API-layer only: rolls up every project in a workspace (reusing consolidated_report's per-run normalization) into a ranked cross-project comparison, HTML/PDF template included (pure logic)
api.py                  FastAPI: /analyze, /project, /report(+/html,+/pdf), /auth/*(request-code,verify-code,me,accept-terms,member-login), /analyze/exact, /config, /admin/*, /author, /download, /models, /health, /profile, /workspaces/*(+/members/*(/disable,/enable,/regenerate-code), +/comparison(.pdf)), /projects/*(+/tools/*/runs(+/pdf), +/notes, +/report/consolidated(.pdf))
frontend/index.html    try-it-out page (analyze + Standard/Exact toggle + BYOK key panel + traffic projection + load-test demo)
frontend/report.html   load-test report viewer (HTML view + PDF download), fed by the demo section above
frontend/admin.html    password-gated admin page: test-mode/BYOK toggle + deployment checklist + author-profile editor
frontend/about.html    "What's AIRI?" — living usage guide, updated whenever a new scenario ships
frontend/developers.html  API quick reference + compiled-core-library download button (GET /download; source is not distributed)
frontend/author.html   public founder/about page, built entirely from the admin-edited author profile
frontend/privacy.html  privacy policy, linked from every page's footer
frontend/terms.html    terms & conditions, linked from every page's footer — also what the one-time post-sign-in acceptance gate points to
frontend/workspaces.html  signed-in workspaces/projects app (Phase 1: CRUD + app-key delete confirmation; Phase 2: AIRI tools runnable per project with saved run history; Phase 3: Dashboard/Notes/Actions tabs + downloadable consolidated PDF report; Phase 4: cross-project comparison tab + PDF report + per-run PDF downloads; Phase 5: real team-member access — admin/member roles, access-code sign-in, disable/enable/regenerate)
sql/001_auth_schema.sql  Neon schema for the "Exact" flavor's users/otp_codes tables
sql/002_app_config.sql  Neon schema for the admin-configurable settings table (test-mode + author profile)
sql/003_workspaces_schema.sql  Neon schema for user_profile/workspaces/workspace_members/projects
sql/004_project_tool_runs.sql  Neon schema for saved per-project AIRI tool runs
sql/005_project_notes.sql  Neon schema for a project's notes/comments history
sql/006_workspace_member_access.sql  Neon schema adding real team-member access (user_id/status/access_code_hash) to workspace_members
sql/007_terms_acceptance.sql  Neon schema adding terms_accepted_at to users, for the account-wide Terms & Conditions gate
tests/                  sanity checks for every module above
docs/                   API.md (full endpoint reference), INTEGRATION.md (pipeline integration), EXACT_MODE.md (auth + exact-mode setup + terms gate), ADMIN.md (test-mode/BYOK admin page), WORKSPACES.md (workspaces/projects Phases 1-5)
```

The core library never imports FastAPI, and never makes a network call
that's required for it to function — see "Tokenizer accuracy" below.

## API contract

At a glance — full reference with `/project` and error formats in
[docs/API.md](docs/API.md).

`POST /analyze`

```json
{
  "prompt": "Explain quantum computing simply.",
  "model": "gpt-4o",
  "expected_output_tokens": 500
}
```

(or pass `"messages": [{"role": "system", "content": "..."}, ...]` instead of `prompt`)

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
  "known_model": true,
  "fresh_input_tokens": 8,
  "cache_write_tokens": 0,
  "cache_read_tokens": 0,
  "fresh_input_cost": 0.00002,
  "cache_write_cost": 0.0,
  "cache_read_cost": 0.0,
  "output_cost": 0.005,
  "cache_savings": 0.0,
  "cache_pricing_known": true
}
```

`status` is `SAFE` under 80% of the context window, `WARNING` at 80%+,
`EXCEEDED` past 100%. `GET /models` lists everything in the registry for
a dropdown; an unrecognized model still returns an estimate, just
flagged `confidence: "low"` and `known_model: false` instead of erroring.

## Cache-aware pricing

Token count alone understates real-world cost once prompt caching is in
the picture: a chat app resending growing history isn't paying the
plain input price for every token on every turn — a stable prefix
(system prompt, prior turns) usually gets cached, and cached tokens are
billed at a different rate (a steep discount to read, sometimes a
premium to write). Pass `cache_write_tokens`/`cache_read_tokens` on
`/analyze`, `/analyze/exact`, or per-archetype on `/project` (both
default to `0` — a plain request prices exactly as before this existed)
and the response breaks `estimated_cost` out into `fresh_input_cost` +
`cache_write_cost` + `cache_read_cost` + `output_cost`, plus a
`cache_savings` figure — positive means caching saved money on this
request, negative is normal on a write-only request (the saving shows
up on later reads of that cache entry, not the request that created
it). AIRI doesn't detect caching for you; it only prices it once you
say how many tokens were involved, and a model with no published cache
price in `airi/registry.py` bills those tokens at the plain input rate
instead (`cache_pricing_known: false`), never a guessed discount. The
try-it page's Standard/Exact tool and Traffic projection section both
have a "+ Using prompt caching?" toggle that exposes this.

## Volume projection: estimating cost across a whole app

`analyze()` prices one request. A real app only sends some fraction of
its traffic to a model, split across a handful of distinct call-sites
(a chat reply, a doc summary, a search rerank...) — and AIRI has no way
to know how many of each you'll actually get. That number is your own
traffic data or product projections, not something a token-estimation
library can see. `project()` doesn't try to guess it; it takes the
volume as an input and does the multiplication for you.

```python
from airi import project, Archetype

result = project([
    Archetype(name="chat reply", volume=10000, prompt="Hi there, how can I help?",
              model="gpt-4o-mini", expected_output_tokens=80),
    Archetype(name="doc summary", volume=500, prompt="Summarize this document...",
              model="claude-3-5-sonnet", expected_output_tokens=300),
])
print(result.to_dict())
```

Each archetype is one distinct AI call-site: a representative sample
request (same `prompt`/`messages` + `model` + `expected_output_tokens`
shape `analyze()` takes) plus the volume you expect for it, in whatever
period you're planning for (daily, monthly — AIRI doesn't care, it's
just a multiplier). The result gives per-archetype projected tokens and
cost, a grand total, and a cost/token breakdown by model — useful the
moment two archetypes use different providers. Each archetype can also
carry `cache_write_tokens`/`cache_read_tokens` (see "Cache-aware
pricing" above) — a repeated archetype run many times is exactly where
caching a fixed system prompt or history prefix pays off most, and the
result's `total_cache_savings` sums it across every archetype.
`POST /project` exposes the same thing over HTTP, and the try-it page
has a "Traffic projection" section that builds the request for you.

This is deliberately not the same thing as the frozen spec's P7
"Predictive Intelligence" (P50/P90/P99 output-length prediction from
*observed historical* usage) — that needs a database tracking real
traffic over time. This is simpler and stays honest about its inputs:
you supply the volume assumption, it does the arithmetic. No tracking,
no history, still fully stateless.

## Load-test token usage reporting

A load-testing tool reports CPU, memory, latency — but nothing about
what a run would actually cost against a real model, because it has no
idea what an "AI request" even is. AIRI does, if it's already wired
into the service calls being load-tested (see **Integrating AIRI**
above): every call your test suite makes already produces an
`/analyze` result. `build_report()` (or `POST /report`) just
consolidates whatever your harness collected during the run into one
report — same "you supply the numbers, AIRI does the arithmetic"
pattern as `project()`, extended to results you already have instead
of a volume you're estimating.

The flow, matching how a testing team actually works:

1. Dev team integrates `analyze()`/`/analyze` into the service calls
   being load-tested (per [docs/INTEGRATION.md](docs/INTEGRATION.md)).
2. Testing team runs their suite — any tool, any language (k6, JMeter,
   Locust, a homegrown script) — across normal and peak load scenarios.
3. Each time the suite calls a guarded service, it already gets back an
   AIRI result; the harness tags it with a `label` (which service) and
   optionally a `phase` (`"normal"`/`"peak"`) and `timestamp`, and keeps
   the list.
4. At the end of the run, `POST` the whole list once to `/report` (JSON
   summary), `/report/html` (a rendered page), or `/report/pdf` (a
   downloadable, professional PDF — same template as the HTML view, via
   `xhtml2pdf`).

```python
import requests

AIRI_URL = "http://localhost:8000"

records = []  # append one /analyze result per request, tagged, as your run executes
# records.append({**analyze_result, "label": "checkout-summary", "phase": "peak", "timestamp": "2026-09-13T10:00:05Z"})

resp = requests.post(f"{AIRI_URL}/report", json={"run_name": "Nightly load test — checkout flow", "records": records})
report = resp.json()
print(report["total_requests"], report["total_cost"], report["status_counts"])
```

The report gives totals, an average per request, a SAFE/WARNING/EXCEEDED
breakdown, rollups by label/model/phase, the single peak request, and a
list of the worst-offending flagged requests — nothing is stored on the
server, so run it as many times as you like with whatever you collected.
The try-it page's third section ("Load-test token usage report") is a
self-contained demo of the whole flow — it simulates a couple of
services under normal/peak load, calls the real API, and hands off to
`frontend/report.html` to show the HTML report plus a "Download PDF"
button. Full endpoint reference (record shape, size limits, error
cases): [docs/API.md](docs/API.md).

## Tokenizer accuracy

OpenAI models are counted exactly via `tiktoken`. Every other model
(Claude, Gemini, unknown models) uses a `chars / 4` heuristic — a
reasonable approximation for English text, not exact. `method` and
`confidence` in every response tell you which one you got.

One nuance worth knowing: `tiktoken` fetches its encoding tables over
the network the first time each one is used on a machine, then caches
them locally. Per the spec's "no mandatory network calls from the
core" rule, that fetch is never required — if it fails (offline,
blocked egress, first run in a sandboxed environment), AIRI silently
falls back to the heuristic rather than raising. You'll get exact
counts on a normal machine with outbound internet; you'll get the
heuristic (still usable, just `medium`/not `high` confidence) in air-gapped
or locked-down environments.

## "Exact" flavor: real Claude/Gemini token counts, opt-in

Everything above is the default flavor: exact for OpenAI (tiktoken),
heuristic for everyone else, zero network dependency. The try-it page
also offers an opt-in **Exact** toggle that calls Claude's and Gemini's
own token-counting APIs directly instead of estimating — gated behind a
one-time email code, not because the provider calls cost anything (they
don't), but to keep some Anthropic/Google API key from being hammered
by anonymous traffic. Fully optional, fully separate from the core
library, and designed to run at zero cost even at peak traffic — every
piece (Neon, Resend, the provider APIs themselves) fails safe into a
pause or a heuristic fallback rather than ever generating a bill. See
[docs/EXACT_MODE.md](docs/EXACT_MODE.md) for the full design and setup.

Whose key gets used is a runtime setting: a password-gated admin page
(`frontend/admin.html`) toggles between AIRI's own shared **testing**
keys and requiring each signed-in user to **bring their own** key
(BYOK) — see [docs/ADMIN.md](docs/ADMIN.md).

## Pricing & context data

`airi/registry.py` is one plain table: model → context window, input/output
price per 1M tokens, tokenizer family. Add a model by adding a row.
Prices are example values, approximately current as of early 2026 —
providers change pricing without notice, so treat these as a starting
point to verify, not a live feed.

## What was cut from the frozen spec (and why)

The frozen doc scopes P0–P4 as "MVP" (core library, SDK packaging, web
API with API keys/rate limiting, marketing site, accounts + usage
dashboard). You asked for something leaner: a library, one endpoint,
one try-it page. Cut for that reason:

- **Accounts, registration, login, usage dashboards (P4).** No database,
  no persisted users — the endpoint is stateless.
- **API keys, hashing, revocation, rate limiting (part of P2).** No auth
  layer. Fine for a local/internal tool or an internal try-it page;
  add a key check in `api.py` before exposing this publicly.
- **Marketing/product site pages (P3).** Only the analyzer page exists;
  no docs/pricing/landing pages.
- **Everything past P4** — actual-vs-estimated tracking, predictive
  P50/P90/P99 output-length modeling, spend dashboards, optimization
  recommendations, multi-tenant/enterprise (P5–P10). All of that needs
  the accounts + persistence layer this build deliberately skips.
- **Structured/tool-definition token counting.** The spec lists tool
  schemas as an input type; this build only counts `prompt` text and
  `messages` (covers plain prompts and full conversation history,
  which is most real usage). Tool-schema-aware counting is a
  reasonable next addition if you need it.

Everything that *is* here — `analyze()`, exact/heuristic token counting,
context-window classification, cost estimation, the result shape — matches
the frozen spec's sections 1–5 exactly.

`project()`/`/project` (volume projection, above) isn't in the frozen
spec at all — it's a small, deliberately stateless extension added after
the initial build to answer "what will this cost across my whole app,"
not a scope change to the P0–P4 MVP itself.
