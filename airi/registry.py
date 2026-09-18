"""
Static model registry: context window size, per-token pricing, and which
tokenizer family to use for each supported model.

This is plain data on purpose — no network calls, no database. Keeping it
in one small table is what makes AIRI easy to extend: add a model by
adding a row here.

Prices are USD per 1,000,000 tokens and are approximate examples current
as of early 2026. Providers change pricing often — treat these as
starting values to verify/update for production use, not as a live feed.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelSpec:
    context_window: int
    input_price_per_1m: float
    output_price_per_1m: float
    # "openai" -> counted exactly via tiktoken, locally, no network call.
    # Anything else -> heuristic (chars/4) in the core/default flavor.
    tokenizer_family: str = "heuristic"
    # Which provider's API this model belongs to — "openai" | "anthropic" |
    # "google". Separate from tokenizer_family: tokenizer_family says how
    # the *default* flavor counts (tiktoken vs heuristic); provider says
    # which real API the opt-in "Exact" flavor calls for a non-OpenAI
    # model (see airi/exact_provider.py, API-layer only). OpenAI models
    # don't need a provider call for exact mode — tiktoken already is exact.
    provider: str = "unknown"
    # --- prompt caching (see airi/pricing.py) ---
    #
    # A real chat app resending growing history rarely pays the plain
    # input_price_per_1m for every token — providers let (or make) you
    # cache a stable prefix (system prompt, prior turns) instead. None
    # here means "no separate cache price published/modeled for this
    # model" — cached tokens then fall back to input_price_per_1m, same
    # as if caching weren't used at all (never a crash, just no discount).
    #
    # cache_write_price_per_1m: cost to write NEW content into the cache
    # (Anthropic: ~1.25x input, 5-min TTL — a longer 1h TTL exists at a
    # different rate but isn't modeled here). None for a model whose
    # provider caches automatically with no separate write charge
    # (OpenAI's current published pricing; may change).
    cache_write_price_per_1m: Optional[float] = None
    # cache_read_price_per_1m: cost per token read from an existing cache
    # entry (a cache "hit") — this is where the real savings are, usually
    # a steep discount off input_price_per_1m (roughly 90% off across
    # Anthropic, OpenAI and Gemini as currently published). Gemini also
    # bills a separate per-token-hour storage fee that isn't modeled here
    # (a genuinely different cost dimension — time-based, not per-request).
    cache_read_price_per_1m: Optional[float] = None


MODEL_REGISTRY = {
    # --- OpenAI (exact tokenizer via tiktoken — no provider call needed for Exact mode) ---
    # Automatic prompt caching (prompts over ~1024 tokens): reads at 0.1x
    # input price, no separate write charge on these models as currently
    # published — verify against OpenAI's own pricing page before relying
    # on this for a specific model, same caution as everywhere else in
    # this registry.
    "gpt-4o": ModelSpec(128_000, 2.50, 10.00, "openai", "openai", cache_read_price_per_1m=0.25),
    "gpt-4o-mini": ModelSpec(128_000, 0.15, 0.60, "openai", "openai", cache_read_price_per_1m=0.015),
    "gpt-4-turbo": ModelSpec(128_000, 10.00, 30.00, "openai", "openai", cache_read_price_per_1m=1.00),
    "gpt-4": ModelSpec(8_192, 30.00, 60.00, "openai", "openai", cache_read_price_per_1m=3.00),
    "gpt-3.5-turbo": ModelSpec(16_385, 0.50, 1.50, "openai", "openai", cache_read_price_per_1m=0.05),
    "o1": ModelSpec(200_000, 15.00, 60.00, "openai", "openai", cache_read_price_per_1m=1.50),
    "o1-mini": ModelSpec(128_000, 1.10, 4.40, "openai", "openai", cache_read_price_per_1m=0.11),

    # --- Anthropic (heuristic by default; Exact mode calls /v1/messages/count_tokens) ---
    # Prompt caching (5-min TTL, the default): writes at 1.25x input
    # price, reads at 0.1x — a longer 1h TTL exists at a different write
    # rate but isn't modeled here.
    "claude-3-5-sonnet": ModelSpec(200_000, 3.00, 15.00, "heuristic", "anthropic", cache_write_price_per_1m=3.75, cache_read_price_per_1m=0.30),
    "claude-3-5-haiku": ModelSpec(200_000, 0.80, 4.00, "heuristic", "anthropic", cache_write_price_per_1m=1.00, cache_read_price_per_1m=0.08),
    "claude-3-opus": ModelSpec(200_000, 15.00, 75.00, "heuristic", "anthropic", cache_write_price_per_1m=18.75, cache_read_price_per_1m=1.50),
    "claude-3-haiku": ModelSpec(200_000, 0.25, 1.25, "heuristic", "anthropic", cache_write_price_per_1m=0.3125, cache_read_price_per_1m=0.025),

    # --- Google (heuristic by default; Exact mode calls Gemini's countTokens) ---
    # Context caching: reads at 0.1x input price. Gemini also bills a
    # separate per-token-hour storage fee for cached content, which isn't
    # a per-request cost and isn't modeled in cache_write_price_per_1m —
    # left None here rather than approximated as a per-token price.
    "gemini-1.5-pro": ModelSpec(2_000_000, 1.25, 5.00, "heuristic", "google", cache_read_price_per_1m=0.125),
    "gemini-1.5-flash": ModelSpec(1_000_000, 0.075, 0.30, "heuristic", "google", cache_read_price_per_1m=0.0075),
    "gemini-2.0-flash": ModelSpec(1_000_000, 0.10, 0.40, "heuristic", "google", cache_read_price_per_1m=0.01),
}

# Fallback used when the requested model isn't in the registry, so the
# tool still returns a (clearly low-confidence) estimate instead of an error.
DEFAULT_SPEC = ModelSpec(8_192, 0.0, 0.0, "heuristic", "unknown")

# Context utilization thresholds
WARNING_THRESHOLD = 0.8  # >= 80% of context window


def get_model_spec(model: str) -> ModelSpec:
    return MODEL_REGISTRY.get(model, DEFAULT_SPEC)


def is_known_model(model: str) -> bool:
    return model in MODEL_REGISTRY


def list_supported_models() -> list:
    return sorted(MODEL_REGISTRY.keys())
