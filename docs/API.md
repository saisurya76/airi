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

## Cold starts (demo host only)

If you're hitting `https://airi-mvp-api.onrender.com`, Render's free
tier spins the service down after a period of inactivity. The first
request after that can take 30–60 seconds while it wakes up — this
is Render's behavior, not an AIRI bug. Set a generous timeout (60s+)
and consider a warm-up request if you're demoing it live. A self-hosted
instance on a paid plan, or any always-on host, doesn't have this
issue.
