"""
Token counting: exact where a real tokenizer is available (OpenAI models,
via tiktoken), heuristic fallback everywhere else.

No network calls. tiktoken's encodings ship with the package / are cached
locally after first install, so this stays offline-safe.
"""

from typing import Tuple

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only if tiktoken isn't installed
    _TIKTOKEN_AVAILABLE = False

# Small per-message overhead OpenAI documents for chat-formatted requests
# (role + separators). Applied once per message when exact-counting.
_TOKENS_PER_MESSAGE = 4


def _heuristic_count(text: str) -> int:
    """~4 characters per token is the standard rough estimate for English
    text against GPT-style tokenizers. Good enough for a SAFE/WARNING/
    EXCEEDED signal; not a substitute for a real tokenizer."""
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def count_tokens(text: str, model: str, tokenizer_family: str) -> Tuple[int, str, str]:
    """Returns (token_count, method, confidence) for a single string.

    tiktoken's encoding tables are fetched over the network on first use
    per-machine and then cached locally. The core's "no mandatory network
    calls" rule means that fetch can never be a hard requirement, so any
    failure here (offline, blocked egress, unknown encoding, cache miss)
    falls back to the heuristic estimate instead of raising."""
    if tokenizer_family == "openai" and _TIKTOKEN_AVAILABLE:
        try:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return len(encoding.encode(text or "")), "tokenizer", "high"
        except Exception:
            pass  # fall through to heuristic — e.g. no network to fetch the encoding

    return _heuristic_count(text), "heuristic", "medium"


def count_messages(messages: list, model: str, tokenizer_family: str) -> Tuple[int, str, str]:
    """Returns (token_count, method, confidence) for a list of
    {"role": ..., "content": ...} messages (system/user/assistant history)."""
    total = 0
    method, confidence = "heuristic", "medium"
    for message in messages:
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        tokens, method, confidence = count_tokens(content, model, tokenizer_family)
        total += tokens
        if method == "tokenizer":
            total += _TOKENS_PER_MESSAGE
    return total, method, confidence
