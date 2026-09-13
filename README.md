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
  (`/analyze`, `/project`, `/report`, `/models`, `/health`): request/response
  schemas and error formats.
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
`python3 tests/test_projector.py`, and `python3 tests/test_report.py`.

## Project layout

```
airi/                   core library — zero web/db/cloud dependencies
  analyzer.py             analyze() — per-request estimate
  projector.py             project() — multi-archetype volume projection, built on analyze()
  report.py                 build_report() — consolidate a load-test run's analyze() results
  report_render.py            render_report_html() — one HTML template, also fed to xhtml2pdf
  tokenizer.py              exact counts (tiktoken) with heuristic fallback
  pricing.py                 cost = tokens x registry price
  registry.py                  model -> context window, price, tokenizer family
  models.py                     AnalysisResult
api.py                  FastAPI: POST /analyze, POST /project, POST /report(+/html,+/pdf), GET /models, GET /health
frontend/index.html    try-it-out page (analyze + traffic projection + load-test demo)
frontend/report.html   load-test report viewer (HTML view + PDF download), fed by the demo section above
tests/                  sanity checks for analyzer.py, projector.py, and report.py
docs/                   API.md (full endpoint reference), INTEGRATION.md (pipeline integration guide)
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
  "known_model": true
}
```

`status` is `SAFE` under 80% of the context window, `WARNING` at 80%+,
`EXCEEDED` past 100%. `GET /models` lists everything in the registry for
a dropdown; an unrecognized model still returns an estimate, just
flagged `confidence: "low"` and `known_model: false` instead of erroring.

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
moment two archetypes use different providers. `POST /project` exposes
the same thing over HTTP, and the try-it page has a "Traffic projection"
section that builds the request for you.

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
