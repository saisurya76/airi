# Integrating AIRI into an AI request pipeline

AIRI's whole purpose is to sit in front of the point in your code where
you're about to call an LLM, and answer one question first: *is this
request safe to send?* This doc covers how to wire that in, whatever
your app is written in.

## The pattern

Wherever your code currently does "build a prompt → call the model,"
insert a check in between:

```
build request  →  AIRI: analyze()  →  decide  →  call the model (or don't)
```

`analyze()` (or `POST /analyze`) gives you back a `status` of `SAFE`,
`WARNING`, or `EXCEEDED`. What to do with each is your call, but a
sensible default:

| status | meaning | typical action |
|---|---|---|
| `SAFE` | comfortably under the context window (<80%) | send it |
| `WARNING` | ≥80% of the context window | send it, but log/alert — you're close to a hard failure |
| `EXCEEDED` | over 100% — the provider **will** reject this | don't send it. Trim the request (drop old conversation history, shorten the prompt, chunk the document) and re-check |

Also worth checking `confidence`: it's `"high"` only when an exact
tokenizer was used (currently OpenAI models via `tiktoken`). For every
other model you get a `chars/4` heuristic estimate — still useful for
catching a request that's wildly over budget, but treat numbers near a
threshold with more caution when `confidence` is `"medium"` or `"low"`.
A common approach is to lower your own `WARNING` threshold (e.g. 70%
instead of 80%) for non-`"high"`-confidence checks.

There are two ways to call AIRI, depending on your stack.

## Option A: Python — import the library directly

If your app is already Python, skip the network hop entirely and call
`analyze()` in-process. This is the fastest option and has zero
external dependencies beyond `pip install` (`tiktoken` optionally
fetches its encoding table over the network on first use per machine,
then caches it — see the README's "Tokenizer accuracy"; it never blocks
`analyze()` from working).

```python
from airi import analyze
from openai import OpenAI

client = OpenAI()

def guarded_chat_completion(prompt: str, model: str = "gpt-4o", expected_output_tokens: int = 500, **kwargs):
    """Pre-flight check before spending money on a real completion."""
    check = analyze(prompt=prompt, model=model, expected_output_tokens=expected_output_tokens)

    if check.status == "EXCEEDED":
        raise ValueError(
            f"Request would exceed {model}'s context window "
            f"({check.estimated_total_tokens}/{check.context_window} tokens). "
            "Shorten the prompt or trim conversation history before sending."
        )

    if check.status == "WARNING":
        print(f"[airi] {model} at {check.context_utilization:.0%} of context window — proceeding")

    if check.confidence != "high":
        print(f"[airi] token count is a {check.confidence}-confidence estimate ({check.method})")

    return client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        **kwargs,
    )
```

**MODIFY in practice** — instead of just rejecting an oversized request,
trim conversation history until it fits:

```python
from airi import analyze

def fit_to_context(messages, model="gpt-4o", expected_output_tokens=500, max_utilization=0.8):
    """Drop the oldest non-system message repeatedly until the request
    is comfortably under max_utilization, or there's nothing left to drop."""
    trimmed = list(messages)
    while True:
        check = analyze(messages=trimmed, model=model, expected_output_tokens=expected_output_tokens)
        if check.context_utilization <= max_utilization or len(trimmed) <= 1:
            return trimmed, check
        for i, m in enumerate(trimmed):
            if m.get("role") != "system":
                trimmed.pop(i)
                break
        else:
            return trimmed, check  # nothing left but system messages
```

## Option B: any language — call the HTTP API

Self-host AIRI's API (see the main [README](../README.md#quick-start-running-locally))
and call it like any other internal service. **Don't point production
traffic at the public demo host** (`airi-mvp-api.onrender.com`) — it's
a free-tier instance meant for trying AIRI out, with no auth, no rate
limiting, and cold starts (see [docs/API.md](API.md#cold-starts-demo-host-only)).

**curl** (works the same in any language with an HTTP client):

```bash
curl -s -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain quantum computing simply.", "model": "gpt-4o", "expected_output_tokens": 500}'
```

**Python (`requests`)**, for a Python app that would rather call AIRI
as a separate service than import it (e.g. it runs elsewhere):

```python
import requests

AIRI_URL = "http://localhost:8000"

def check_request(prompt, model="gpt-4o", expected_output_tokens=500):
    resp = requests.post(
        f"{AIRI_URL}/analyze",
        json={"prompt": prompt, "model": model, "expected_output_tokens": expected_output_tokens},
        timeout=60,  # generous: see cold-start note above if pointed at a free-tier host
    )
    resp.raise_for_status()
    return resp.json()

result = check_request("Explain quantum computing simply.")
if result["status"] == "EXCEEDED":
    raise ValueError(f"Request too large for {result['model']}")
```

**Node.js / JavaScript (`fetch`)**:

```javascript
const AIRI_URL = process.env.AIRI_URL || "http://localhost:8000";

async function guardedChatCompletion(openai, prompt, { model = "gpt-4o", expectedOutputTokens = 500 } = {}) {
  const res = await fetch(`${AIRI_URL}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, model, expected_output_tokens: expectedOutputTokens }),
  });
  const check = await res.json();

  if (check.status === "EXCEEDED") {
    throw new Error(
      `Request would exceed ${model}'s context window (${check.estimated_total_tokens}/${check.context_window} tokens).`
    );
  }
  if (check.status === "WARNING") {
    console.warn(`[airi] ${model} at ${(check.context_utilization * 100).toFixed(0)}% of context window`);
  }

  return openai.chat.completions.create({
    model,
    messages: [{ role: "user", content: prompt }],
  });
}
```

Any other language follows the same shape: `POST` a JSON body to
`/analyze`, read back `status`/`context_utilization`/`estimated_cost`,
decide before calling your actual model provider.

## Capacity planning: projecting cost across your whole app

The per-request check above answers "is *this* request safe." For
"what will my AI spend look like this month," use `/project` (or
`airi.project()` directly in Python) — see the README's "Volume
projection" section for the concept. A typical use: a script you run
by hand, in CI, or on a schedule, forecasting cost from your own
traffic numbers rather than live per-request calls:

```python
import requests

AIRI_URL = "http://localhost:8000"

archetypes = [
    {"name": "chat reply", "volume": 300_000, "prompt": "A typical support chat message.",
     "model": "gpt-4o-mini", "expected_output_tokens": 80},
    {"name": "doc summary", "volume": 15_000, "prompt": "Summarize this document in three bullet points.",
     "model": "claude-3-5-sonnet", "expected_output_tokens": 300},
]

resp = requests.post(f"{AIRI_URL}/project", json={"archetypes": archetypes}, timeout=60)
result = resp.json()

print(f"Projected volume this period: {result['total_volume']:,} requests")
print(f"Projected cost: ${result['total_cost']:.2f}")
for model, cost in result["cost_by_model"].items():
    print(f"  {model}: ${cost:.2f}")
if result["any_exceeded"]:
    print("WARNING: at least one request type already exceeds its context window on its own — fix the sample, not the volume.")
```

Update `volume` from your own analytics whenever they change; AIRI
doesn't track usage over time on its own (see the README's note on
why this is deliberately different from usage-tracking/prediction
features).

## Summary

- In-process Python → `from airi import analyze` — no network hop.
- Anything else → self-hosted `POST /analyze` over HTTP.
- Check `status` before every real model call: `EXCEEDED` blocks,
  `WARNING` logs, `SAFE` proceeds.
- Check `confidence` too — heuristic estimates deserve a safety margin.
- Use `/project` for planning, not per-request gating — it needs a
  volume number you supply, not a live request.
- Never point another application's production traffic at the public
  demo host.
