"""
Real exact token counting for Anthropic/Google models, via each
provider's own (free, no-charge) token-counting API — used only by the
opt-in, login-gated "Exact" flavor (see docs/EXACT_MODE.md).

This is deliberately NOT part of the default path: the core library and
the default /analyze endpoint stay exactly as network-optional as
before (heuristic chars/4 for Claude/Gemini, tiktoken for OpenAI — see
airi/tokenizer.py). This module is only reached from api.py's
/analyze/exact endpoint, after a signed-in check, using AIRI's own
server-held provider keys (env vars) — never a per-user BYOK key.

Like report_render.py, db.py and email_provider.py, this is an
API-layer concern and is never imported by airi/__init__.py.
"""

from typing import List, Optional

import httpx

_TIMEOUT_SECONDS = 15.0

ANTHROPIC_COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
ANTHROPIC_VERSION = "2023-06-01"
GOOGLE_COUNT_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens?key={api_key}"


class ExactCountUnavailable(RuntimeError):
    """Raised whenever a real provider count couldn't be obtained — network
    error, bad response, missing key. The API layer catches this and falls
    back to the heuristic estimate (same graceful-degradation pattern as
    tiktoken's own network fallback in tokenizer.py), surfacing that
    fallback honestly in the response rather than failing the request."""


def _as_messages(prompt: Optional[str], messages: Optional[List[dict]]) -> List[dict]:
    if messages:
        return messages
    return [{"role": "user", "content": prompt or ""}]


def _split_system(messages: List[dict]):
    """Pulls any role="system" entries out into a separate system-prompt
    string (both providers count system instructions separately from the
    user/assistant turns), returning (system_text, conversation_messages).
    Falls back to a single empty user turn if nothing's left — both
    providers require at least one content entry."""
    system_parts, convo = [], []
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content", "") or ""
        if role == "system":
            system_parts.append(content)
        else:
            convo.append({"role": role, "content": content})
    if not convo:
        convo = [{"role": "user", "content": ""}]
    return "\n\n".join(system_parts), convo


def count_tokens_anthropic(prompt: Optional[str], messages: Optional[List[dict]], model: str, api_key: str) -> int:
    """Exact input-token count via Anthropic's /v1/messages/count_tokens —
    free, doesn't create a message, doesn't consume any billed tokens."""
    system_text, convo = _split_system(_as_messages(prompt, messages))
    body = {
        "model": model,
        "messages": [{"role": m["role"] if m["role"] in ("user", "assistant") else "user", "content": m["content"]} for m in convo],
    }
    if system_text:
        body["system"] = system_text

    try:
        resp = httpx.post(
            ANTHROPIC_COUNT_URL,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": ANTHROPIC_VERSION,
                "X-Api-Key": api_key,
            },
            json=body,
            timeout=_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ExactCountUnavailable(f"Could not reach Anthropic's token-counting API: {exc}") from exc

    if resp.status_code >= 400:
        raise ExactCountUnavailable(f"Anthropic count_tokens returned {resp.status_code}: {resp.text[:300]}")

    try:
        return int(resp.json()["input_tokens"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ExactCountUnavailable(f"Unexpected response shape from Anthropic count_tokens: {exc}") from exc


def count_tokens_google(prompt: Optional[str], messages: Optional[List[dict]], model: str, api_key: str) -> int:
    """Exact input-token count via Gemini's countTokens — free, doesn't
    consume generation quota. Tries the richer generateContentRequest
    shape first (so a system prompt is actually counted as a system
    instruction, matching how it'd really be billed); if that request
    shape is rejected, falls back to the plain `contents`-only shape
    (folding any system text into the first turn) rather than failing
    the whole exact-mode request over one provider's schema pickiness."""
    system_text, convo = _split_system(_as_messages(prompt, messages))
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in convo
    ]
    model_path = model if model.startswith("models/") else f"models/{model}"
    url = GOOGLE_COUNT_URL_TEMPLATE.format(model=model_path.split("/", 1)[1], api_key=api_key)

    rich_body = {"generateContentRequest": {"model": model_path, "contents": contents}}
    if system_text:
        rich_body["generateContentRequest"]["systemInstruction"] = {"parts": [{"text": system_text}]}

    try:
        resp = httpx.post(url, json=rich_body, timeout=_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        raise ExactCountUnavailable(f"Could not reach Google's token-counting API: {exc}") from exc

    if resp.status_code >= 400:
        # Fold system text into the conversation and retry with the plainer
        # shape before giving up — see docstring.
        fallback_contents = contents
        if system_text:
            fallback_contents = [{"role": "user", "parts": [{"text": system_text}]}] + contents
        try:
            resp = httpx.post(url, json={"contents": fallback_contents}, timeout=_TIMEOUT_SECONDS)
        except httpx.HTTPError as exc:
            raise ExactCountUnavailable(f"Could not reach Google's token-counting API: {exc}") from exc
        if resp.status_code >= 400:
            raise ExactCountUnavailable(f"Google countTokens returned {resp.status_code}: {resp.text[:300]}")

    try:
        return int(resp.json()["totalTokens"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ExactCountUnavailable(f"Unexpected response shape from Google countTokens: {exc}") from exc


_PROVIDER_FUNCS = {
    "anthropic": count_tokens_anthropic,
    "google": count_tokens_google,
}


def has_exact_provider(provider: str) -> bool:
    """True for providers exact_provider knows how to call (Anthropic,
    Google). OpenAI models don't need this — tiktoken already gives an
    exact count locally, for free, with no network call."""
    return provider in _PROVIDER_FUNCS


def count_tokens_exact(prompt: Optional[str], messages: Optional[List[dict]], model: str, provider: str, api_key: str) -> int:
    """Dispatches to the right provider's exact-count call. Raises
    ExactCountUnavailable (never a provider-specific exception) so
    callers have one exception type to catch and fall back on."""
    func = _PROVIDER_FUNCS.get(provider)
    if func is None:
        raise ExactCountUnavailable(f"No exact-count provider available for '{provider}'.")
    return func(prompt, messages, model, api_key)
