# llm.py — Handles LLM integration and prompt execution.
pass
import json
import logging
import re
import threading
import config

logger = logging.getLogger("filium.llm")
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
    "invalid token"
)

class LLMUnavailable(RuntimeError):
    pass

def _note_failure(exc: Exception) -> None:
    pass
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
        "answers and templated trade explanations for "
        "the rest of this process. Fix the key or top up the account and "
        "restart to re-enable it.",
        str(exc)[:200])

def degraded_reason() -> str | None:
    pass
    return _degraded

def reset_degraded() -> None:
    pass
    global _degraded
    with _degrade_lock:
        _degraded = None

def is_configured() -> bool:
    pass
    return config.LLM_ENABLED and _degraded is None

def get_client():
    pass
    global _client
    if not config.LLM_ENABLED:
        raise LLMUnavailable(
            "No text-generation model is configured — set LLM_API_KEY "
            "(and LLM_BASE_URL for a non-OpenAI provider) to enable AI-generated text")
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
    pass
    global _client
    _client = None

def complete(prompt: str, system: str | None = None, temperature: float = 0.0, max_tokens: int | None = None) -> str:
    pass
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        response = client.chat.completions.create(
            model = config.LLM_MODEL,
            messages = messages,
            temperature = temperature,
            max_tokens = max_tokens)
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        _note_failure(exc)
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

def _parse_json_loosely(text: str) -> dict:
    pass
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
    pass
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
            logger.info("Provider rejected response_format; retrying without it: %s", str(exc)[:200])
            response = _call(False)
        return _parse_json_loosely(response.choices[0].message.content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMUnavailable(f"LLM returned malformed JSON: {exc}") from exc
    except Exception as exc:
        _note_failure(exc)
        raise LLMUnavailable(f"LLM request failed: {exc}") from exc

def _rejects_response_format(exc: Exception) -> bool:
    pass
    text = str(exc).lower()
    return "response_format" in text or "response format" in text or ("json_object" in text)

def try_complete(prompt: str, system: str | None = None, **kwargs) -> str | None:
    pass
    try:
        return complete(prompt, system = system, **kwargs)
    except LLMUnavailable as exc:
        logger.info("Skipping LLM generation: %s", exc)
        return None

def embed(texts: list[str]) -> list[list[float]]:
    pass
    import embeddings
    try:
        return embeddings.embed_documents(texts)
    except embeddings.EmbeddingUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc

def embed_query(text: str) -> list[float]:
    pass
    import embeddings
    try:
        return embeddings.embed_query(text)
    except embeddings.EmbeddingUnavailable as exc:
        raise LLMUnavailable(str(exc)) from exc