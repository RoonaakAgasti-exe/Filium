"""
llm.py

A single, guarded entry point to the OpenAI API.

Everything LLM-backed in this app (RAG answers, trade explanations,
natural-language alert parsing) is an enhancement over a feature that
still works without it. So the rule here is: never raise into a request
path just because a key is missing or a provider call failed. Callers get
`None` (or a documented fallback) and decide what to show.
"""

import json
import logging

import config

logger = logging.getLogger("fincopilot.llm")

_client = None


class LLMUnavailable(RuntimeError):
    """No OpenAI key configured, or the provider call failed."""


def is_configured() -> bool:
    return config.OPENAI_ENABLED


def get_client():
    """Lazily constructs the OpenAI client so importing this module never needs a key."""
    global _client
    if not config.OPENAI_ENABLED:
        raise LLMUnavailable(
            "OpenAI is not configured — set OPENAI_API_KEY to enable AI-generated text"
        )

    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise LLMUnavailable(f"openai package is not installed: {exc}") from exc
        _client = OpenAI(api_key=config.OPENAI_API_KEY)

    return _client


def complete(prompt: str, system: str | None = None, temperature: float = 0.0,
             max_tokens: int | None = None) -> str:
    """Plain text completion. Raises LLMUnavailable on any failure."""
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc


def complete_json(prompt: str, system: str | None = None) -> dict:
    """
    Completion constrained to a JSON object. Used where the result feeds
    straight into code (alert rule parsing) rather than being shown to a
    person.
    """
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    try:
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"LLM returned malformed JSON: {exc}") from exc
    except Exception as exc:
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc


def try_complete(prompt: str, system: str | None = None, **kwargs) -> str | None:
    """complete(), but swallows failures and returns None. For optional prose."""
    try:
        return complete(prompt, system=system, **kwargs)
    except LLMUnavailable as exc:
        logger.info("Skipping LLM generation: %s", exc)
        return None


def embed(texts: list[str]) -> list[list[float]]:
    """
    Embeds a batch of texts. Chunked to stay under the per-request input
    limit — a single 10-K section can produce more chunks than one
    embeddings call accepts, and the resulting error is a hard failure
    partway through ingestion rather than something retryable.
    """
    client = get_client()
    max_per_request = 96
    out: list[list[float]] = []

    for start in range(0, len(texts), max_per_request):
        batch = texts[start:start + max_per_request]
        try:
            response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=batch)
        except Exception as exc:
            raise LLMUnavailable(f"Embedding request failed: {exc}") from exc
        out.extend(item.embedding for item in response.data)

    return out
