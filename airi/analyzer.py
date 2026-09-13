"""
Core analyzer: the one thing this whole library does.

analyze() takes a request description (a plain prompt, or a list of
chat messages) plus a target model, and returns token / context / cost
estimates so the caller can decide to SEND / MODIFY / REJECT before
spending money on the real API call.

Independent of any web framework, database or cloud SDK by design —
this module must keep working with nothing installed but its own
package (tiktoken is an optional accelerator, not a requirement).
"""

from typing import List, Optional, Union

from .models import AnalysisResult
from .pricing import estimate_cost
from .registry import WARNING_THRESHOLD, get_model_spec, is_known_model
from .tokenizer import count_messages, count_tokens

Message = dict  # {"role": "system" | "user" | "assistant", "content": str}


def analyze(
    prompt: Optional[str] = None,
    messages: Optional[List[Message]] = None,
    model: str = "gpt-4o",
    expected_output_tokens: int = 0,
) -> AnalysisResult:
    """
    Estimate tokens, context usage and cost for an AI request.

    Args:
        prompt: a plain-text prompt. Use this OR `messages`, not both.
        messages: a chat-style list of {"role", "content"} dicts —
            covers system/user messages and conversation history.
        model: target model name (see airi.list_supported_models()).
            Unknown models still get an estimate, flagged low-confidence.
        expected_output_tokens: caller-supplied estimate of the response
            size. AIRI does not predict this for you (that's a separate,
            harder problem) — it only accounts for it once you provide it.

    Returns:
        AnalysisResult
    """
    if prompt is None and not messages:
        raise ValueError("Provide either `prompt` or `messages`.")
    if prompt is not None and messages:
        raise ValueError("Provide only one of `prompt` or `messages`, not both.")

    spec = get_model_spec(model)

    if messages:
        input_tokens, method, confidence = count_messages(messages, model, spec.tokenizer_family)
    else:
        input_tokens, method, confidence = count_tokens(prompt, model, spec.tokenizer_family)

    return build_result_from_counts(model, input_tokens, method, confidence, expected_output_tokens)


def build_result_from_counts(
    model: str,
    input_tokens: int,
    method: str,
    confidence: str,
    expected_output_tokens: int = 0,
) -> AnalysisResult:
    """
    Given an input-token count someone else already computed — the local
    tiktoken/heuristic path above, or a real provider API call — fills in
    the rest of the estimate: context usage, SAFE/WARNING/EXCEEDED status,
    and cost. `analyze()` is a thin wrapper around this that also does the
    counting itself.

    This is what lets the API layer's opt-in "Exact" flavor (see
    api.py's /analyze/exact, and airi/exact_provider.py) reuse the exact
    same status/cost math as the default flavor after getting input_tokens
    from a real provider API instead of the local heuristic — one place
    decides what SAFE/WARNING/EXCEEDED means, however the count was
    obtained.
    """
    spec = get_model_spec(model)
    known = is_known_model(model)
    if not known:
        confidence = "low"

    estimated_output_tokens = max(0, int(expected_output_tokens or 0))
    estimated_total_tokens = input_tokens + estimated_output_tokens

    context_utilization = (
        round(estimated_total_tokens / spec.context_window, 4) if spec.context_window else 0.0
    )

    if estimated_total_tokens > spec.context_window:
        status = "EXCEEDED"
    elif context_utilization >= WARNING_THRESHOLD:
        status = "WARNING"
    else:
        status = "SAFE"

    cost = estimate_cost(input_tokens, estimated_output_tokens, spec)

    return AnalysisResult(
        model=model,
        input_tokens=input_tokens,
        estimated_output_tokens=estimated_output_tokens,
        estimated_total_tokens=estimated_total_tokens,
        context_window=spec.context_window,
        context_utilization=context_utilization,
        estimated_cost=cost,
        method=method,
        confidence=confidence,
        status=status,
        known_model=known,
    )
