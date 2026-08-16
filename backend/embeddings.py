# embeddings.py — Vector embedding generation and management.
pass
import logging
import threading
import config

logger = logging.getLogger("filium.embeddings")

OPENAI_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536
}

LOCAL_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384
}

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_local_model = None
_local_lock = threading.Lock()
_openai_degraded: str | None = None
_degrade_lock = threading.Lock()
_corpus_pin: str | None = None
_PERMANENT_OPENAI_FAILURES = (
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_not_active",
    "account_deactivated",
    "invalid_api_key",
    "incorrect api key",
    "401",
    "403",
    "429")

class EmbeddingUnavailable(RuntimeError):
    pass

class CorpusMismatch(RuntimeError):
    pass

def _note_openai_failure(exc: Exception) -> None:
    pass
    global _openai_degraded
    text = str(exc).lower()
    if not any(marker in text for marker in _PERMANENT_OPENAI_FAILURES):
        return
    with _degrade_lock:
        if _openai_degraded is not None:
            return
        _openai_degraded = str(exc)
    logger.warning(
        "OpenAI embeddings are unusable (%s). Falling back to the local model "
        "'%s' for anything not already embedded. Set EMBEDDING_PROVIDER=openai "
        "to fail loudly instead, or fix the key to switch back.",
        str(exc)[:200], config.LOCAL_EMBEDDING_MODEL)

def openai_degraded_reason() -> str | None:
    pass
    return _openai_degraded

def provider_for_model(name: str) -> str | None:
    pass
    if name in OPENAI_DIMENSIONS:
        return "openai"
    if name in LOCAL_DIMENSIONS:
        return "local"
    return None

def _pin_to_corpus(model: str) -> None:
    pass
    global _corpus_pin
    with _degrade_lock:
        if _corpus_pin == model:
            return
        _corpus_pin = model
    logger.warning(
        "Adopting '%s' for retrieval: it is what embedded the stored chunks. "
        "Set EMBEDDING_PROVIDER explicitly to override, or re-ingest to change model.",
        model)

def reset_degraded_state() -> None:
    pass
    global _openai_degraded, _corpus_pin, _embed_client
    with _degrade_lock:
        _openai_degraded = None
        _corpus_pin = None
        _embed_client = None

def active_provider() -> str:
    pass
    configured = config.EMBEDDING_PROVIDER
    if configured in ("openai", "local"):
        return configured
    if not config.REMOTE_EMBEDDINGS_AVAILABLE:
        return "local"
    if _corpus_pin:
        pinned = provider_for_model(_corpus_pin)
        if pinned:
            return pinned
    return "local" if _openai_degraded else "openai"

def model_name() -> str:
    if _corpus_pin and config.EMBEDDING_PROVIDER not in ("openai", "local"):
        if provider_for_model(_corpus_pin):
            return _corpus_pin
    return config.EMBEDDING_MODEL if active_provider() == "openai" else config.LOCAL_EMBEDDING_MODEL

def dimension() -> int:
    name = model_name()
    table = OPENAI_DIMENSIONS if active_provider() == "openai" else LOCAL_DIMENSIONS
    if name not in table:
        raise EmbeddingUnavailable(
            f"Unknown embedding dimension for '{name}'. Add it to "
            f"embeddings.py so the vector() column width can be checked "
            f"before anything is written."
        )
    return table[name]

def describe() -> dict:
    pass
    info = {"provider": active_provider(), "model": model_name(), "dimension": dimension()}
    if _openai_degraded:
        info["fell_back_from_openai"] = _openai_degraded[:300]
    if _corpus_pin:
        info["pinned_to_corpus_model"] = _corpus_pin
    return info

def _get_local_model():
    pass
    global _local_model
    if _local_model is not None:
        return _local_model
    with _local_lock:
        if _local_model is not None:
            return _local_model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailable(
                "Local embeddings need sentence-transformers: "
                "pip install sentence-transformers"
            ) from exc
        name = config.LOCAL_EMBEDDING_MODEL
        logger.info("Loading local embedding model %s (first run downloads it)", name)
        try:
            _local_model = SentenceTransformer(name)
        except Exception as exc:
            raise EmbeddingUnavailable(f"Could not load local model {name}: {exc}") from exc
        return _local_model

def _embed_local(texts: list[str]) -> list[list[float]]:
    model = _get_local_model()
    try:
        vectors = model.encode(texts, normalize_embeddings = True, show_progress_bar = False)
    except Exception as exc:
        raise EmbeddingUnavailable(f"Local embedding failed: {exc}") from exc
    return [v.tolist() for v in vectors]

_embed_client = None

def _get_embedding_client():
    pass
    global _embed_client
    if _embed_client is not None:
        return _embed_client
    if not config.REMOTE_EMBEDDINGS_AVAILABLE:
        raise EmbeddingUnavailable(
            "No remote embedding endpoint is configured. The text model's "
            "LLM_BASE_URL serves chat completions only; set EMBEDDING_BASE_URL "
            "and EMBEDDING_API_KEY to use a remote embedding service, or leave "
            "EMBEDDING_PROVIDER=auto to use the local model."
        )

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise EmbeddingUnavailable(f"openai package is not installed: {exc}") from exc
    kwargs = {"api_key": config.EMBEDDING_API_KEY}
    if config.EMBEDDING_BASE_URL:
        kwargs["base_url"] = config.EMBEDDING_BASE_URL
    _embed_client = OpenAI(**kwargs)
    return _embed_client

def _embed_openai(texts: list[str]) -> list[list[float]]:
    client = _get_embedding_client()
    max_per_request = 96
    out: list[list[float]] = []
    for start in range(0, len(texts), max_per_request):
        batch = texts[start:start + max_per_request]
        try:
            response = client.embeddings.create(model = config.EMBEDDING_MODEL, input = batch)
        except Exception as exc:
            _note_openai_failure(exc)
            raise EmbeddingUnavailable(f"Embedding request failed: {exc}") from exc
        out.extend(item.embedding for item in response.data)
    return out

def embed_documents(texts: list[str]) -> list[list[float]]:
    pass
    if not texts:
        return []
    if active_provider() != "openai":
        return _embed_local(texts)
    try:
        return _embed_openai(texts)
    except EmbeddingUnavailable:
        if active_provider() == "openai":
            raise
        return _embed_local(texts)

def embed_query(text: str) -> list[float]:
    pass
    if active_provider() == "openai":
        try:
            return _embed_openai([text])[0]
        except EmbeddingUnavailable:
            if active_provider() == "openai":
                raise
    prepared = text
    if "bge" in config.LOCAL_EMBEDDING_MODEL.lower():
        prepared = BGE_QUERY_PREFIX + text
    return _embed_local([prepared])[0]

def corpus_model(conn) -> str | None:
    pass
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT embedding_model FROM filing_chunks "
            "WHERE embedding_model IS NOT NULL LIMIT 1"
        )
        row = cur.fetchone()
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()
    return row[0] if row else None

def assert_matches_corpus(conn) -> None:
    pass
    stored = corpus_model(conn)
    if stored is None:
        return
    active = model_name()
    if stored == active:
        return
    if config.EMBEDDING_PROVIDER not in ("openai", "local") and provider_for_model(stored):
        _pin_to_corpus(stored)
        if model_name() == stored:
            return
    raise CorpusMismatch(
        f"Filing chunks were embedded with '{stored}' but the active "
        f"embedding model is '{active}'. Vectors from different models "
        f"are not comparable. Either set EMBEDDING_PROVIDER so '{stored}' "
        f"is active again, or re-ingest the filings with the new model "
        f"(see db/migrations/004_embedding_provider.sql)."
    )