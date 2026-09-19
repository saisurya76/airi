"""
Live AI Guide per gate — real, billed generation calls to Anthropic's
/v1/messages or Google's :generateContent, turning a CoE gate's static
`guide_question` (see airi/workspaces.py's COE_GATES) into a short,
project-specific checklist.

This is a different animal from airi/exact_provider.py, even though it's
modeled closely on its shape (same _TIMEOUT_SECONDS-style constant, same
one-exception-type-for-callers pattern, same "never echo raw provider
response text back to the caller" discipline). exact_provider.py only
ever calls each provider's FREE token-counting endpoint, using AIRI's own
server-held key in test mode. This module makes a REAL completion call —
there is no free tier and no AIRI-held key involved at all: it is always
BYOK (see POST /projects/{id}/coe-phases/{gate_key}/ai-guide in api.py,
which rejects the request with a 400 before this module is even reached
if neither key is present), by explicit product decision: this feature
must never spend AIRI's own money, and must never be mandatory to use
CoE governance.

Like exact_provider.py, this is an API-layer concern — never imported by
airi/__init__.py — and every function here raises one exception type
(AiGuideUnavailable) so api.py has exactly one thing to catch.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import json

import httpx

_TIMEOUT_SECONDS = 30.0  # generation is slower than the free count endpoints exact_provider.py calls

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
GOOGLE_GENERATE_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

# Deliberately fixed, cheap, fast models — not user-selectable. This
# feature is a lightweight opt-in add-on ("must not be mandatory," per
# the product decision), not a place to make someone pick a model before
# they can get a checklist; and since it's always the caller's own key
# (see module docstring), a frugal default is the considerate one.
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
DEFAULT_GOOGLE_MODEL = "gemini-2.0-flash"

MIN_CHECKLIST_ITEMS = 1
MAX_CHECKLIST_ITEMS = 7
MAX_ITEM_CHARS = 300  # a checklist entry, not an essay — also bounds what lands in coe_gate_ai_guides


class AiGuideUnavailable(RuntimeError):
    """Raised whenever a checklist couldn't be produced — network error,
    bad/unexpected response shape, or a response that didn't parse as the
    JSON array of strings the prompt asked for. api.py turns this into a
    502 (a real generation call failing isn't the caller's own request
    being wrong, so this isn't a 400) — there's no heuristic fallback to
    silently degrade to, unlike exact_provider.py falling back to the
    chars/4 estimate, since there's no non-AI way to produce a tailored
    checklist at all."""


@dataclass
class GateGuideContext:
    """Everything the prompt needs, pulled once in api.py from the
    project row + ws.COE_GATES_BY_KEY/ACCOUNTABLE_ROLES/enforcement_level
    so this module stays free of any workspaces.py or db.py import."""

    project_title: str
    project_description: str
    project_type: str
    tech_stack: Dict[str, str] = field(default_factory=dict)
    risk_tier: str = "low"
    risk_explanation: str = ""
    gate_label: str = ""
    guide_question: str = ""
    accountable_role_label: str = ""
    enforcement_level: str = "advisory"


_SYSTEM_PROMPT = (
    "You help small teams apply a lightweight AI governance process (a "
    "\"Center of Excellence\", or CoE) to a specific project. Given a "
    "project's context and the single governance gate the team is "
    "currently working through, produce a short, concrete, PROJECT-"
    "SPECIFIC checklist for that gate. Do not restate the gate's guide "
    "question, and do not give generic best-practice advice that would "
    "apply to any project — name the actual tech, data, and risk "
    "specifics you were given wherever it changes what the team should "
    "check. Respond with ONLY a JSON array of 3 to 7 short strings and "
    "nothing else — no markdown, no code fences, no numbering, no prose "
    "before or after the array. Each string is one concrete, actionable "
    "checklist item, under 200 characters."
)


def _tech_stack_text(tech_stack: Dict[str, str]) -> str:
    if not tech_stack:
        return "(not specified)"
    return ", ".join(f"{k}: {v}" for k, v in tech_stack.items() if v)


def _build_user_prompt(context: GateGuideContext) -> str:
    return (
        f"Project: {context.project_title or '(untitled)'}\n"
        f"Project type: {context.project_type or '(not specified)'}\n"
        f"Description: {context.project_description or '(none given)'}\n"
        f"Tech stack: {_tech_stack_text(context.tech_stack)}\n"
        f"Risk tier: {context.risk_tier}"
        + (f" — {context.risk_explanation}" if context.risk_explanation else "")
        + "\n"
        f"Gate: {context.gate_label} — {context.guide_question}\n"
        f"Accountable role for this gate: {context.accountable_role_label}\n"
        f"Enforcement level at this project's current risk tier: {context.enforcement_level}\n\n"
        "Give the tailored checklist now, as a JSON array of strings only."
    )


def _parse_checklist(text: str) -> List[str]:
    """Tolerant of a model that ignores "nothing else" and wraps the
    array in a code fence or a sentence — tries a straight parse first,
    then falls back to the first-'['-to-last-']' substring, before giving
    up. Never includes the raw model text in what it raises (see
    AiGuideUnavailable's docstring) — only a description of what went
    wrong."""
    candidates = [text.strip()]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])

    items: Optional[List[Any]] = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            items = parsed
            break

    if items is None:
        raise AiGuideUnavailable("The model didn't return a checklist in the expected format.")

    cleaned = [str(item).strip() for item in items if str(item or "").strip()]
    cleaned = [item[:MAX_ITEM_CHARS] for item in cleaned][:MAX_CHECKLIST_ITEMS]
    if len(cleaned) < MIN_CHECKLIST_ITEMS:
        raise AiGuideUnavailable("The model returned an empty checklist.")
    return cleaned


def generate_gate_checklist_anthropic(context: GateGuideContext, api_key: str, model: str = DEFAULT_ANTHROPIC_MODEL) -> List[str]:
    body = {
        "model": model,
        "max_tokens": 700,
        "system": _SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": _build_user_prompt(context)}],
    }
    try:
        resp = httpx.post(
            ANTHROPIC_MESSAGES_URL,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
                "X-Api-Key": api_key,
            },
            json=body,
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise AiGuideUnavailable(f"Could not reach Anthropic's API: {exc}") from exc

    if resp.status_code >= 400:
        raise AiGuideUnavailable(f"Anthropic returned an error (status {resp.status_code}) while generating the guide.")

    try:
        blocks = resp.json()["content"]
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    except (KeyError, TypeError, AttributeError) as exc:
        raise AiGuideUnavailable(f"Unexpected response shape from Anthropic: {exc}") from exc

    return _parse_checklist(text)


def generate_gate_checklist_google(context: GateGuideContext, api_key: str, model: str = DEFAULT_GOOGLE_MODEL) -> List[str]:
    model_path = model if model.startswith("models/") else f"models/{model}"
    url = GOOGLE_GENERATE_URL_TEMPLATE.format(model=model_path.split("/", 1)[1], api_key=api_key)
    body = {
        "contents": [{"role": "user", "parts": [{"text": _build_user_prompt(context)}]}],
        "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "generationConfig": {"maxOutputTokens": 700, "temperature": 0.2},
    }
    try:
        resp = httpx.post(url, json=body, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise AiGuideUnavailable(f"Could not reach Google's API: {exc}") from exc

    if resp.status_code >= 400:
        raise AiGuideUnavailable(f"Google returned an error (status {resp.status_code}) while generating the guide.")

    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise AiGuideUnavailable(f"Unexpected response shape from Google: {exc}") from exc

    return _parse_checklist(text)


_PROVIDER_FUNCS = {
    "anthropic": generate_gate_checklist_anthropic,
    "google": generate_gate_checklist_google,
}


def has_ai_guide_provider(provider: str) -> bool:
    return provider in _PROVIDER_FUNCS


def generate_gate_checklist(context: GateGuideContext, provider: str, api_key: str) -> List[str]:
    """Dispatches to the right provider's checklist call. Raises
    AiGuideUnavailable (never a provider-specific exception) so api.py
    has one exception type to catch."""
    func = _PROVIDER_FUNCS.get(provider)
    if func is None:
        raise AiGuideUnavailable(f"No AI guide provider available for '{provider}'.")
    return func(context, api_key)
