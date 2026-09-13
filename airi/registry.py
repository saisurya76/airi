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
    # "openai" -> counted exactly via tiktoken. Anything else -> heuristic.
    tokenizer_family: str = "heuristic"


MODEL_REGISTRY = {
    # --- OpenAI (exact tokenizer via tiktoken) ---
    "gpt-4o": ModelSpec(128_000, 2.50, 10.00, "openai"),
    "gpt-4o-mini": ModelSpec(128_000, 0.15, 0.60, "openai"),
    "gpt-4-turbo": ModelSpec(128_000, 10.00, 30.00, "openai"),
    "gpt-4": ModelSpec(8_192, 30.00, 60.00, "openai"),
    "gpt-3.5-turbo": ModelSpec(16_385, 0.50, 1.50, "openai"),
    "o1": ModelSpec(200_000, 15.00, 60.00, "openai"),
    "o1-mini": ModelSpec(128_000, 1.10, 4.40, "openai"),

    # --- Anthropic (heuristic — no public exact tokenizer library) ---
    "claude-3-5-sonnet": ModelSpec(200_000, 3.00, 15.00, "heuristic"),
    "claude-3-5-haiku": ModelSpec(200_000, 0.80, 4.00, "heuristic"),
    "claude-3-opus": ModelSpec(200_000, 15.00, 75.00, "heuristic"),
    "claude-3-haiku": ModelSpec(200_000, 0.25, 1.25, "heuristic"),

    # --- Google (heuristic) ---
    "gemini-1.5-pro": ModelSpec(2_000_000, 1.25, 5.00, "heuristic"),
    "gemini-1.5-flash": ModelSpec(1_000_000, 0.075, 0.30, "heuristic"),
    "gemini-2.0-flash": ModelSpec(1_000_000, 0.10, 0.40, "heuristic"),
}

# Fallback used when the requested model isn't in the registry, so the
# tool still returns a (clearly low-confidence) estimate instead of an error.
DEFAULT_SPEC = ModelSpec(8_192, 0.0, 0.0, "heuristic")

# Context utilization thresholds
WARNING_THRESHOLD = 0.8  # >= 80% of context window


def get_model_spec(model: str) -> ModelSpec:
    return MODEL_REGISTRY.get(model, DEFAULT_SPEC)


def is_known_model(model: str) -> bool:
    return model in MODEL_REGISTRY


def list_supported_models() -> list:
    return sorted(MODEL_REGISTRY.keys())
