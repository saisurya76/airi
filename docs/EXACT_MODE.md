# The "Exact" flavor: real provider token counts, gated by email sign-in

The default AIRI flavor (everything in [README.md](../README.md) and
[API.md](API.md) up to this point) counts OpenAI models exactly via
`tiktoken` and estimates everyone else (Claude, Gemini) with a chars/4
heuristic — by design, since the core library never makes a network
call it doesn't absolutely need.

"Exact" is an opt-in second flavor for the single-request analyzer on
the try-it page: for Claude/Gemini models, it calls that provider's own
token-counting API instead of guessing. It sits entirely at the API
layer (`airi/exact_provider.py`, `api.py`) — the core library
(`airi.analyze()`) is completely unchanged and still has zero mandatory
network dependency.

## Why sign-in, if the provider calls are free?

They are — Anthropic's `/v1/messages/count_tokens` and Google's
Gemini `countTokens` are both genuinely free, don't consume any billed
tokens, and never auto-charge even under heavy use. The reason Exact
mode is login-gated isn't cost, it's that AIRI holds **its own**
Anthropic/Google API keys server-side (not a per-user key), and those
keys have their own rate limits (requests-per-minute) shared across
everyone using this deployment. A one-time email code keeps that shared
capacity from being hammered by anonymous/scripted traffic. There's no
password, no plan tiers, nothing paywalled — it's an abuse guard, not a
monetization gate.

## Why this can run at zero cost, even at peak traffic

Every piece was picked specifically so that hitting its free-tier limit
degrades the experience rather than ever generating a bill:

| Piece | Free tier | What happens over the limit |
|---|---|---|
| Neon (Postgres) | 0.5 GB storage, ~100 compute-hours/month, autosuspends after 5 min idle | Compute pauses until next billing cycle; **never auto-charges** |
| Resend (email) | 100 emails/day (~3,000/month) | Stops sending further emails that day; **never auto-charges** |
| Anthropic count_tokens | Free, no token cost | Rate-limited (RPM by usage tier); a 429 there triggers AIRI's own graceful fallback to the heuristic |
| Google countTokens | Free, no token cost | Shares quota with generation; same graceful fallback on failure |
| Render (API host) | Free web service, cold starts after idle | Slower first request; no charge |

Every one of these fails "closed but safe" — into a queue, a pause, or
a heuristic fallback — never into a surprise invoice. The one thing to
actually watch operationally is Resend's 100/day cap, since that's the
smallest number in the table (see "Rate limits" below).

## One-time setup

You'll need four things before Exact mode works. None of this is
required for the rest of AIRI — `/analyze`, `/project`, `/report*` all
work today with zero of this configured.

### 1. Neon database

1. Create a free project at [neon.tech](https://neon.tech).
2. Run the schema once: `psql "$DATABASE_URL" -f sql/001_auth_schema.sql`
   (use the connection string Neon gives you — prefer the **pooled**
   connection string, since a free-tier Render instance opening many
   short-lived connections is exactly what Postgres connection pooling
   is for).
3. Set `DATABASE_URL` on Render to that same connection string.

### 2. Resend (email)

Resend's shared sandbox domain can only deliver to *your own* Resend
account email — sending OTP codes to real users requires a verified
domain:

1. In the Resend dashboard, add a domain you control — a subdomain is
   fine and recommended (e.g. `mail.yourdomain.com`), so it's isolated
   from anything else that domain does.
2. Add the DNS records Resend gives you (SPF/DKIM) at your DNS
   provider. Verification is usually quick once the records propagate.
3. Set on Render:
   - `RESEND_API_KEY` — from the Resend dashboard
   - `RESEND_FROM_EMAIL` — e.g. `AIRI <otp@mail.yourdomain.com>`, on
     the domain you just verified

### 3. Anthropic / Google API keys

Server-held, shared keys — not per-user:

- `ANTHROPIC_API_KEY` — from console.anthropic.com. Only used for the
  free `count_tokens` endpoint; no spend risk from this integration
  specifically.
- `GOOGLE_API_KEY` — from Google AI Studio. Same: only used for the
  free `countTokens` endpoint.

Exact mode for a given provider simply returns a clear `503` until its
key is set — the rest of AIRI is unaffected either way.

### 4. Auth secret

- `AUTH_SECRET` — any long random string, used both to sign session
  tokens (JWTs) and as the hashing pepper for stored OTP codes. Generate
  one with `openssl rand -hex 32`. Treat it like a password: rotating it
  invalidates every signed-in session (harmless — everyone just signs in
  again) and, more importantly, changing it after codes were issued
  invalidates those codes' hashes (also harmless — codes are single-use
  and expire in 10 minutes anyway).

## The sign-in flow

1. `POST /auth/request-code` `{"email": "..."}` — generates a 6-digit
   code, stores its hash (never the raw code) in Neon with a 10-minute
   expiry, and emails it via Resend. Always returns the same generic
   success message regardless of whether the email is new, to avoid
   leaking who's used AIRI before.
2. `POST /auth/verify-code` `{"email": "...", "code": "..."}` — checks
   the code against the stored hash. On success, returns a signed
   session token (JWT, 30-day expiry) and marks the code consumed
   (single-use). On failure, increments an attempt counter (max 5
   attempts per code).
3. Store the token client-side (the try-it page uses `localStorage`)
   and send it as `Authorization: Bearer <token>` on
   `POST /analyze/exact` and `GET /auth/me`.

There is deliberately no session table — a session is just a valid
signed JWT, so checking one costs zero database round trips. The
database is only touched at login time (requesting/verifying a code),
never on every `/analyze/exact` call.

Full request/response reference for all four endpoints:
[docs/API.md](API.md#exact-flavor-auth--analyzeexact).

## Rate limits (built in, not configurable via env vars)

| Limit | Value | Why |
|---|---|---|
| OTP request cooldown | 1 per 60 seconds, per email | Stops accidental double-submits and rapid retries |
| OTP daily cap | 5 per email, per rolling 24h | Protects Resend's shared 100/day quota — 20 different emails could otherwise exhaust it between them in one day |
| Exact-mode calls | 20 per signed-in user, per minute | Protects the shared Anthropic/Google keys' own rate limits |

The exact-mode limiter is in-process (resets on a redeploy) — that's
intentional: it's an abuse guard, not a security boundary, and keeping
it out of the database means a normal `/analyze/exact` call never adds
a DB round trip.

## Maintenance

`otp_codes` rows aren't deleted automatically (Neon's free tier is 0.5
GB, which is a lot of 6-digit-code rows, but not infinite). Run
occasionally, e.g. from a scheduled job hitting a small maintenance
script:

```python
from airi.db import delete_old_codes
delete_old_codes()  # removes rows older than 7 days by default
```

## What this does *not* change

- `/analyze`, `/project`, `/report`, `/report/html`, `/report/pdf` are
  completely unaffected — no auth, no new dependencies at runtime,
  exactly as documented elsewhere.
- The core `airi` library (`analyze()`, `project()`, `build_report()`)
  still has zero mandatory network calls and zero dependency on
  Postgres/Resend/Anthropic/Google being configured.
- Exact mode currently applies to the single-request analyzer only —
  the traffic-projection and load-test-report sections keep using the
  default (heuristic/tiktoken) flavor, since extending Exact mode to
  bulk calls needs its own, stricter rate-limit design.
