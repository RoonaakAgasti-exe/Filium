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
import re
import threading

import config

logger = logging.getLogger("fincopilot.llm")

_client = None

_degraded: str | None = None
_degrade_lock = threading.Lock()

_PERMANENT_FAILURES = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_not_active",
    "billing_hard_limit_reached",
    "account_deactivated",
    "invalid_api_key",
    "incorrect api key",
    "no auth credentials",
    "user not found",
    "insufficient credits",
    "invalid model",
    "model not found",
    "invalid token",
)

class LLMUnavailable(RuntimeError):
    """No OpenAI key configured, or the provider call failed."""

def _note_failure(exc: Exception) -> None:
    """
    Latches "the LLM is unusable" when the provider says so permanently.

    `config.OPENAI_ENABLED` can only check the *shape* of a key, never its
    balance, so a key that is expired, revoked, or out of credit reads as
    configured and then fails on every call. Without this latch each such
    call still pays the SDK's full retry ladder before giving up, and
    every LLM-backed path in the app is a request path: asking a question,
    parsing an alert, buying a share. Measured against an out-of-credit
    key that was ~3s of dead latency added to each of those, on top of an
    answer that was going to come from the fallback regardless.

    The fallbacks themselves are what make latching safe: nothing here is
    load-bearing. RAG answers extractively, alert text is parsed by
    keyword, and trade explanations are templated — so switching the LLM
    off early changes when those paths run, never whether they work.

    One process-lifetime latch, not a timed circuit breaker, because the
    conditions listed above are resolved by a human topping up an account
    and are not worth re-probing on a schedule. A restart clears it.
    """
    global _degraded
    text = str(exc).lower()
    if not any(marker in text for marker in _PERMANENT_FAILURES):
        return
    with _degrade_lock:
        if _degraded is not None:
            return
        _degraded = str(exc)
    logger.warning(
        "OpenAI text generation is unusable (%s). Falling back to extractive "
        "answers, keyword alert parsing and templated trade explanations for "
        "the rest of this process. Fix the key or top up the account and "
        "restart to re-enable it.",
        str(exc)[:200],
    )

def degraded_reason() -> str | None:
    """The provider error that disabled generation, or None. Surfaced at /health."""
    return _degraded

def reset_degraded() -> None:
    """Clears the latch. For tests, and after a credential change."""
    global _degraded
    with _degrade_lock:
        _degraded = None

def is_configured() -> bool:
    """
    True when generated prose is actually available.

    Includes the degraded latch, not just the key, because every caller
    asks this question to decide whether to bother — and a key that only
    produces 429s should answer "no" the same way a missing key does.
    """
    return config.LLM_ENABLED and _degraded is None

def get_client():
    """
    Lazily constructs the chat client so importing this module never needs a key.

    The `openai` package is used as a wire-protocol client, not as a vendor
    binding: `base_url` points it at whatever speaks OpenAI-style chat
    completions. That is what lets this app run on TokenRouter, OpenRouter,
    Groq, vLLM or Ollama with nothing changed but two environment variables.
    """
    global _client
    if not config.LLM_ENABLED:
        raise LLMUnavailable(
            "No text-generation model is configured — set LLM_API_KEY "
            "(and LLM_BASE_URL for a non-OpenAI provider) to enable AI-generated text"
        )
    if _degraded is not None:
        raise LLMUnavailable(f"The text model is configured but unusable: {_degraded}")

    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMUnavailable(f"openai package is not installed: {exc}") from exc
        kwargs = {"api_key": config.LLM_API_KEY}
        if config.LLM_BASE_URL:
            kwargs["base_url"] = config.LLM_BASE_URL
        _client = OpenAI(**kwargs)

    return _client

def reset_client() -> None:
    """Drops the cached client. For tests, and after a credential change."""
    global _client
    _client = None

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
        _note_failure(exc)
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

def _parse_json_loosely(text: str) -> dict:
    """
    Reads a JSON object out of a completion.

    `response_format` is an OpenAI extension that many OpenAI-compatible
    gateways accept and quietly ignore, and that some hosted models reject
    outright. Either way the model usually still writes valid JSON — just
    wrapped in a ```json fence or a sentence of preamble. Parsing that back
    out is the difference between natural-language alerts working on a
    non-OpenAI provider and silently falling through to keyword matching.
    """
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            raise
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed

def complete_json(prompt: str, system: str | None = None) -> dict:
    """
    Completion constrained to a JSON object. Used where the result feeds
    straight into code (alert rule parsing) rather than being shown to a
    person.

    Tries the `response_format` hint first and retries once without it if
    the provider rejects the parameter, because that rejection says nothing
    about whether the model can do the task — only that this particular
    gateway doesn't implement an OpenAI extension.
    """
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    def _call(use_response_format: bool):
        kwargs = {"model": config.LLM_MODEL, "messages": messages, "temperature": 0}
        if use_response_format:
            kwargs["response_format"] = {"type": "json_object"}
        return client.chat.completions.create(**kwargs)

    try:
        try:
            response = _call(True)
        except Exception as exc:
            if not _rejects_response_format(exc):
                raise
            logger.info(
                "Provider rejected response_format; retrying without it: %s", str(exc)[:200]
            )
            response = _call(False)
        return _parse_json_loosely(response.choices[0].message.content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMUnavailable(f"LLM returned malformed JSON: {exc}") from exc
    except Exception as exc:
        _note_failure(exc)
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc

def _rejects_response_format(exc: Exception) -> bool:
    """
    True when the provider's error is about the `response_format` parameter
    itself rather than about the request being wrong.

    Matched on the error text because the openai SDK raises the same
    BadRequestError for every 400, and a gateway that doesn't implement the
    parameter is not a condition worth failing the feature over.
    """
    text = str(exc).lower()
    return "response_format" in text or "response format" in text or (
        "json_object" in text
    )

def try_complete(prompt: str, system: str | None = None, **kwargs) -> str | None:
    """complete(), but swallows failures and returns None. For optional prose."""
    try:
        return complete(prompt, system=system, **kwargs)
    except LLMUnavailable as exc:
        logger.info("Skipping LLM generation: %s", exc)
        return None

def embed(texts: list[str]) -> list[list[float]]:
    """
    Embeds a batch of passages.

    Kept as a thin delegate rather than deleted: embeddings no longer
    require OpenAI at all (see embeddings.py), but callers outside this
    module still reach for `llm.embed`, and routing them through here
    means there is exactly one place that decides which model runs.
    """
    import embeddings

    try:
        return embeddings.embed_documents(texts)
    except embeddings.EmbeddingUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc

def embed_query(text: str) -> list[float]:
    """
    Embeds one search query.

    Separate from `embed` because asymmetric models (bge-*, the local
    default) want queries encoded with an instruction prefix that
    passages must not get — embedding a query as though it were a passage
    silently costs recall rather than failing, which is the worst way for
    it to be wrong. Both paths convert EmbeddingUnavailable to
    LLMUnavailable so the request surfaces as a 503 with the provider's
    own explanation, not a 500.
    """
    import embeddings

    try:
        return embeddings.embed_query(text)
    except embeddings.EmbeddingUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc
