"""Result type returned by analyze()."""

from dataclasses import dataclass, asdict


@dataclass
class AnalysisResult:
    model: str
    input_tokens: int
    estimated_output_tokens: int
    estimated_total_tokens: int
    context_window: int
    context_utilization: float
    estimated_cost: float
    method: str          # "tokenizer" | "heuristic"
    confidence: str       # "high" | "medium" | "low"
    status: str            # "SAFE" | "WARNING" | "EXCEEDED"
    known_model: bool       # False if the model wasn't in the registry
    # --- cache-aware cost breakdown (see airi/pricing.py) ---
    #
    # All zero/known-True when the caller didn't specify any cached
    # tokens — a plain request, priced exactly as before this field
    # existed. input_tokens above is still the TOTAL input token count;
    # these three split it into what was actually billed at each rate.
    fresh_input_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    fresh_input_cost: float = 0.0
    cache_write_cost: float = 0.0
    cache_read_cost: float = 0.0
    output_cost: float = 0.0
    # estimated_cost minus what the input side would have cost with none
    # of it cached — positive means caching saved money on this request,
    # negative is normal on a cache-write-only request (see
    # airi.pricing.CostBreakdown.cache_savings).
    cache_savings: float = 0.0
    # False means any cache_write/cache_read tokens passed in were
    # billed at the plain input rate because this model has no published
    # cache price in the registry — not because caching wasn't used.
    cache_pricing_known: bool = True

    def to_dict(self) -> dict:
        return asdict(self)
