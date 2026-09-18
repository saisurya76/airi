"""
Cost estimation from token counts, using the static registry.

Token cost alone is an incomplete answer once prompt caching is in the
picture: a real chat app resending growing history typically isn't
paying the plain input price for every token on every turn — a stable
prefix (system prompt, prior turns) gets cached, and cached tokens are
billed at a different rate (usually a steep discount to read, sometimes
a premium to write). estimate_cost() takes the caller's split of a
request's input into fresh / cache-write / cache-read tokens and prices
each bucket separately, falling back to the plain input rate for any
bucket a model has no published cache price for (see
airi/registry.py:ModelSpec) — never an error, just no discount modeled.
"""

from dataclasses import dataclass

from .registry import ModelSpec


@dataclass
class CostBreakdown:
    """One request's cost, broken out by what each token actually cost —
    not just a single total. `total_cost` is the number that becomes
    AnalysisResult.estimated_cost; the rest is here so a caller can show
    *why* (e.g. "compare with the a fully-fresh request").
    """

    fresh_input_cost: float   # input tokens billed at the plain input rate (no caching)
    cache_write_cost: float   # tokens newly written into the cache this request
    cache_read_cost: float    # tokens served from an existing cache entry (the actual savings)
    output_cost: float
    total_cost: float
    # What this request would have cost if none of its input were
    # cached (all of it at the plain input rate) minus what the input
    # side actually cost. Positive = caching saved money on this
    # request; negative = it didn't (typical on a cache-write-only
    # request, since a write usually costs *more* than fresh input —
    # the saving shows up on later reads of that same cache entry, not
    # on the request that created it).
    cache_savings: float
    # True if this model has a published cache_read price in the
    # registry — False means any cache_read/cache_write tokens passed in
    # were billed at the plain input rate because no discount is known
    # for this model, not because caching wasn't used.
    cache_pricing_known: bool

    def to_dict(self) -> dict:
        return {
            "fresh_input_cost": self.fresh_input_cost,
            "cache_write_cost": self.cache_write_cost,
            "cache_read_cost": self.cache_read_cost,
            "output_cost": self.output_cost,
            "total_cost": self.total_cost,
            "cache_savings": self.cache_savings,
            "cache_pricing_known": self.cache_pricing_known,
        }


def estimate_cost(
    fresh_input_tokens: int,
    output_tokens: int,
    spec: ModelSpec,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> CostBreakdown:
    """
    fresh_input_tokens, cache_write_tokens and cache_read_tokens are
    assumed already disjoint and non-negative (the caller — see
    airi/analyzer.py — is responsible for splitting a request's total
    input tokens into these three buckets and clamping them so they
    can't overcount); this function just prices each bucket.
    """
    cache_pricing_known = spec.cache_read_price_per_1m is not None
    write_rate = spec.cache_write_price_per_1m if spec.cache_write_price_per_1m is not None else spec.input_price_per_1m
    read_rate = spec.cache_read_price_per_1m if spec.cache_read_price_per_1m is not None else spec.input_price_per_1m

    fresh_input_cost = (fresh_input_tokens / 1_000_000) * spec.input_price_per_1m
    cache_write_cost = (cache_write_tokens / 1_000_000) * write_rate
    cache_read_cost = (cache_read_tokens / 1_000_000) * read_rate
    output_cost = (output_tokens / 1_000_000) * spec.output_price_per_1m
    total_cost = fresh_input_cost + cache_write_cost + cache_read_cost + output_cost

    total_input_tokens = fresh_input_tokens + cache_write_tokens + cache_read_tokens
    naive_input_cost = (total_input_tokens / 1_000_000) * spec.input_price_per_1m
    cache_savings = naive_input_cost - (fresh_input_cost + cache_write_cost + cache_read_cost)

    return CostBreakdown(
        fresh_input_cost=round(fresh_input_cost, 6),
        cache_write_cost=round(cache_write_cost, 6),
        cache_read_cost=round(cache_read_cost, 6),
        output_cost=round(output_cost, 6),
        total_cost=round(total_cost, 6),
        cache_savings=round(cache_savings, 6),
        cache_pricing_known=cache_pricing_known,
    )
