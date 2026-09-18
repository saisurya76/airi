"""
Volume projection: "given N requests of each shape, what's the total?"

AIRI's analyze() answers the cost of one request. It has no way to know
how many requests an app will actually send — that's traffic/business
data that lives in the app, not in a token-estimation library. This
module doesn't try to guess it. Instead it takes the volume as an input
you supply (from your own analytics, logs, or projections) alongside a
representative sample of each distinct request shape ("archetype"), and
does the multiplication for you.

Still fully stateless: no history, no tracking, no database. Every
archetype is just run through analyze() once and scaled by its volume.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Union

from .analyzer import analyze
from .models import AnalysisResult

MAX_ARCHETYPES = 50  # generous for a real app's distinct AI call-sites; a guardrail, not a design limit


@dataclass
class Archetype:
    """One distinct place your app calls an AI model, described by a
    representative sample request plus how many of them you expect."""

    name: str
    volume: int  # however many requests you expect of this shape, in whatever period you're planning for
    prompt: Optional[str] = None
    messages: Optional[list] = None
    model: str = "gpt-4o"
    expected_output_tokens: int = 0
    # How many of this ONE representative request's input tokens are
    # cache-write/cache-read (see airi/analyzer.py) — describes the unit
    # request, same as prompt/messages/expected_output_tokens do, and gets
    # scaled by volume along with everything else. A repeated archetype
    # (the same call-site run many times) is exactly where caching is most
    # realistic — e.g. a fixed system prompt cached across every repetition.
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "Archetype":
        return cls(
            name=d["name"],
            volume=d["volume"],
            prompt=d.get("prompt"),
            messages=d.get("messages"),
            model=d.get("model", "gpt-4o"),
            expected_output_tokens=d.get("expected_output_tokens", 0),
            cache_write_tokens=d.get("cache_write_tokens", 0),
            cache_read_tokens=d.get("cache_read_tokens", 0),
        )


@dataclass
class ArchetypeProjection:
    name: str
    model: str
    volume: int
    unit: AnalysisResult  # the per-request estimate this archetype was scaled from
    projected_input_tokens: int
    projected_output_tokens: int
    projected_total_tokens: int
    projected_cost: float
    # unit.cache_savings (see airi/pricing.py) scaled by volume, same as
    # projected_cost is unit.estimated_cost scaled by volume. Zero when
    # the archetype didn't set cache_write_tokens/cache_read_tokens.
    projected_cache_savings: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unit"] = self.unit.to_dict()
        return d


@dataclass
class ProjectionResult:
    archetypes: List[ArchetypeProjection]
    total_volume: int
    total_tokens: int
    total_cost: float
    cost_by_model: dict
    tokens_by_model: dict
    any_exceeded: bool  # True if any single archetype's unit request already exceeds its context window
    # Sum of every archetype's projected_cache_savings. Zero unless at
    # least one archetype set cache_write_tokens/cache_read_tokens.
    total_cache_savings: float = 0.0

    def to_dict(self) -> dict:
        return {
            "archetypes": [a.to_dict() for a in self.archetypes],
            "total_volume": self.total_volume,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "cost_by_model": self.cost_by_model,
            "tokens_by_model": self.tokens_by_model,
            "any_exceeded": self.any_exceeded,
            "total_cache_savings": self.total_cache_savings,
        }


def project(archetypes: List[Union[Archetype, dict]]) -> ProjectionResult:
    """
    Project total tokens and cost across many kinds of AI request at once.

    Args:
        archetypes: one entry per distinct AI call-site in your app. Each
            needs a `name`, a `volume` (however many you expect — daily,
            monthly, whatever period you're planning for), and the same
            `prompt`/`messages` + `model` + `expected_output_tokens`
            shape that analyze() takes for a single request.

    Returns:
        ProjectionResult — per-archetype projections plus grand totals
        and a cost/token breakdown by model.
    """
    if not archetypes:
        raise ValueError("Provide at least one archetype.")
    if len(archetypes) > MAX_ARCHETYPES:
        raise ValueError(f"Provide at most {MAX_ARCHETYPES} archetypes per projection.")

    results = []
    cost_by_model: dict = {}
    tokens_by_model: dict = {}
    total_tokens = 0
    total_cost = 0.0
    total_volume = 0
    any_exceeded = False
    total_cache_savings = 0.0

    for raw in archetypes:
        a = raw if isinstance(raw, Archetype) else Archetype.from_dict(raw)
        if a.volume < 0:
            raise ValueError(f"Archetype '{a.name}': volume must be >= 0.")

        unit = analyze(
            prompt=a.prompt,
            messages=a.messages,
            model=a.model,
            expected_output_tokens=a.expected_output_tokens,
            cache_write_tokens=a.cache_write_tokens,
            cache_read_tokens=a.cache_read_tokens,
        )
        if unit.status == "EXCEEDED":
            any_exceeded = True

        projected_input = unit.input_tokens * a.volume
        projected_output = unit.estimated_output_tokens * a.volume
        projected_total = unit.estimated_total_tokens * a.volume
        projected_cost = round(unit.estimated_cost * a.volume, 6)
        projected_cache_savings = round(unit.cache_savings * a.volume, 6)

        results.append(
            ArchetypeProjection(
                name=a.name,
                model=a.model,
                volume=a.volume,
                unit=unit,
                projected_input_tokens=projected_input,
                projected_output_tokens=projected_output,
                projected_total_tokens=projected_total,
                projected_cost=projected_cost,
                projected_cache_savings=projected_cache_savings,
            )
        )

        total_tokens += projected_total
        total_cost += projected_cost
        total_volume += a.volume
        total_cache_savings += projected_cache_savings
        cost_by_model[a.model] = round(cost_by_model.get(a.model, 0.0) + projected_cost, 6)
        tokens_by_model[a.model] = tokens_by_model.get(a.model, 0) + projected_total

    return ProjectionResult(
        archetypes=results,
        total_volume=total_volume,
        total_tokens=total_tokens,
        total_cost=round(total_cost, 6),
        cost_by_model=cost_by_model,
        tokens_by_model=tokens_by_model,
        any_exceeded=any_exceeded,
        total_cache_savings=round(total_cache_savings, 6),
    )
