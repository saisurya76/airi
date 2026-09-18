"""Quick sanity checks for cache-aware pricing — not exhaustive, just enough
to catch a broken build."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from airi.pricing import estimate_cost
from airi.registry import get_model_spec


def test_no_caching_matches_plain_input_price():
    # cache_write_tokens=cache_read_tokens=0 should price identically to
    # treating the whole thing as fresh input — this is the "existed
    # before this feature" behavior, and it must not have changed.
    spec = get_model_spec("gpt-4o")
    b = estimate_cost(fresh_input_tokens=1000, output_tokens=200, spec=spec)
    expected_input_cost = round((1000 / 1_000_000) * spec.input_price_per_1m, 6)
    expected_output_cost = round((200 / 1_000_000) * spec.output_price_per_1m, 6)
    assert b.fresh_input_cost == expected_input_cost
    assert b.cache_write_cost == 0.0
    assert b.cache_read_cost == 0.0
    assert b.output_cost == expected_output_cost
    assert b.total_cost == round(expected_input_cost + expected_output_cost, 6)
    assert b.cache_savings == 0.0
    print("OK: no_caching_matches_plain_input_price ->", b.to_dict())


def test_cache_read_is_cheaper_than_fresh_on_known_model():
    # claude-3-5-sonnet has a published cache_read discount — reading the
    # same token count from cache should cost less than pricing it fresh.
    spec = get_model_spec("claude-3-5-sonnet")
    fresh = estimate_cost(fresh_input_tokens=10000, output_tokens=0, spec=spec)
    cached = estimate_cost(fresh_input_tokens=0, output_tokens=0, spec=spec, cache_read_tokens=10000)
    assert cached.cache_read_cost < fresh.fresh_input_cost
    assert cached.cache_pricing_known is True
    print("OK: cache_read_is_cheaper_than_fresh_on_known_model ->", fresh.total_cost, cached.total_cost)


def test_cache_write_costs_more_than_fresh_on_anthropic():
    # Anthropic's published cache write is a premium (~1.25x) over plain
    # input — a write-only request should cost MORE than the same tokens
    # priced fresh, and cache_savings should reflect that as negative.
    spec = get_model_spec("claude-3-5-sonnet")
    fresh = estimate_cost(fresh_input_tokens=10000, output_tokens=0, spec=spec)
    written = estimate_cost(fresh_input_tokens=0, output_tokens=0, spec=spec, cache_write_tokens=10000)
    assert written.cache_write_cost > fresh.fresh_input_cost
    assert written.cache_savings < 0
    print("OK: cache_write_costs_more_than_fresh_on_anthropic ->", fresh.total_cost, written.total_cost)


def test_unknown_cache_pricing_falls_back_to_input_rate():
    # A model with no published cache prices (e.g. the DEFAULT_SPEC
    # fallback for an unknown model) should price cache tokens at the
    # plain input rate — never crash, never a phantom discount.
    spec = get_model_spec("some-made-up-model-9000")
    assert spec.cache_read_price_per_1m is None
    assert spec.cache_write_price_per_1m is None
    b = estimate_cost(fresh_input_tokens=0, output_tokens=0, spec=spec, cache_write_tokens=1000, cache_read_tokens=1000)
    assert b.cache_pricing_known is False
    expected_each = round((1000 / 1_000_000) * spec.input_price_per_1m, 6)
    assert b.cache_write_cost == expected_each
    assert b.cache_read_cost == expected_each
    print("OK: unknown_cache_pricing_falls_back_to_input_rate ->", b.to_dict())


def test_cache_savings_positive_on_repeated_read():
    # The realistic "second+ turn of a cached conversation" case: mostly
    # cache reads, a little fresh input for the new turn — should be
    # cheaper overall than pricing all of it fresh, and cache_savings
    # should say so.
    spec = get_model_spec("gpt-4o")
    b = estimate_cost(fresh_input_tokens=50, output_tokens=100, spec=spec, cache_read_tokens=5000)
    naive = estimate_cost(fresh_input_tokens=5050, output_tokens=100, spec=spec)
    assert b.total_cost < naive.total_cost
    assert b.cache_savings > 0
    print("OK: cache_savings_positive_on_repeated_read ->", b.total_cost, naive.total_cost)


if __name__ == "__main__":
    test_no_caching_matches_plain_input_price()
    test_cache_read_is_cheaper_than_fresh_on_known_model()
    test_cache_write_costs_more_than_fresh_on_anthropic()
    test_unknown_cache_pricing_falls_back_to_input_rate()
    test_cache_savings_positive_on_repeated_read()
    print("\nAll pricing sanity checks passed.")
