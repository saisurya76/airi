"""
AIRI — AI Request Intelligence

A lightweight, provider-neutral library that estimates tokens, context
usage and cost for an AI request *before* you send it, so your app can
decide to SEND / MODIFY / REJECT.

Usage:
    from airi import analyze

    result = analyze(
        prompt="Explain quantum computing simply.",
        model="gpt-4o",
        expected_output_tokens=500,
    )
    print(result.to_dict())
"""

from .analyzer import analyze
from .models import AnalysisResult
from .projector import Archetype, ProjectionResult, project
from .registry import list_supported_models

__all__ = [
    "analyze",
    "AnalysisResult",
    "list_supported_models",
    "project",
    "Archetype",
    "ProjectionResult",
]
__version__ = "0.2.0"
