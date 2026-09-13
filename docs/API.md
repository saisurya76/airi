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

## Cold starts (demo host only)

If you're hitting `https://airi-mvp-api.onrender.com`, Render's free
tier spins the service down after a period of inactivity. The first
request after that can take 30–60 seconds while it wakes up — this
is Render's behavior, not an AIRI bug. Set a generous timeout (60s+)
and consider a warm-up request if you're demoing it live. A self-hosted
instance on a paid plan, or any always-on host, doesn't have this
issue.
