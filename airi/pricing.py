"""Cost estimation from token counts, using the static registry."""

from .registry import ModelSpec


def estimate_cost(input_tokens: int, output_tokens: int, spec: ModelSpec) -> float:
    input_cost = (input_tokens / 1_000_000) * spec.input_price_per_1m
    output_cost = (output_tokens / 1_000_000) * spec.output_price_per_1m
    return round(input_cost + output_cost, 6)
