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

    def to_dict(self) -> dict:
        return asdict(self)
