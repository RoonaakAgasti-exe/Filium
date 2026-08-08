"""
embeddings.py

One embedding provider for the whole app, chosen once and recorded in the
database.

Why this exists as its own module rather than living in `llm.py`: the
project's whole design principle is that a clone with an empty `.env`
still works. Prices fall through Alpaca → FMP → Yahoo → stored close, and
news falls through Finnhub → NewsAPI → Google RSS. Retrieval had no such
path — no OpenAI key meant no embeddings, which meant no filing chunks,
which meant the chat feature (the headline feature of the project) was
not merely degraded but entirely absent. A local sentence-transformers
model closes that gap: it needs no key, no account, and no billing, and
for retrieval over filing prose it is genuinely competitive.

The one thing that must NOT be automatic is switching providers on a
database that already holds vectors. Cosine distance between a
`text-embedding-3-small` vector and a `bge-small-en-v1.5` vector is
meaningless — the two models don't share a space — and the dimensions
differ besides (1536 vs 384), so pgvector rejects the comparison outright
rather than returning bad results. Provider selection is therefore sticky:
whatever embedded the corpus must also embed the queries, and
`assert_matches_corpus` turns a mismatch into a sentence telling you to
re-ingest rather than a stack trace from inside the SQL layer.
"""

import logging
import threading

import config

logger = logging.getLogger("fincopilot.embeddings")

OPENAI_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

LOCAL_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
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
    "429",
)

class EmbeddingUnavailable(RuntimeError):
    """No embedding provider could be constructed."""

class CorpusMismatch(RuntimeError):
    """The stored chunks were embedded by a different model than the active one."""

def _note_openai_failure(exc: Exception) -> None:
    """
    Records that OpenAI embeddings are unusable, if the error says so.

    `config.OPENAI_ENABLED` can only check the *shape* of a key, never its
    balance, so a key that is expired, revoked, or simply out of credit
    reads as configured and then 429s on every call. Left alone that is
    worse than having no key at all: with an empty `.env` retrieval drops
    to the local model and the app works, but with a dead key it selects
    OpenAI and every ingest and every question fails.

    Demotion is deliberately narrow. It only applies under
    EMBEDDING_PROVIDER=auto — `auto` means "pick the one that works", so
    picking the one that works is the contract, whereas an explicit
    `openai` is an instruction and gets an honest failure instead. And it
    never risks mixing vector spaces: `assert_matches_corpus` still runs
    on every query, so a corpus already embedded by OpenAI raises a
    CorpusMismatch that names both models rather than silently searching
    with the wrong one. The case this rescues is the one that matters —
    a fresh clone whose corpus is still empty.
    """
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
        str(exc)[:200], config.LOCAL_EMBEDDING_MODEL,
    )

def openai_degraded_reason() -> str | None:
    """The provider error that caused the fallback, or None. Surfaced at /health."""
    return _openai_degraded

def provider_for_model(name: str) -> str | None:
    """Which backend owns a model name, or None if it isn't one we know."""
    if name in OPENAI_DIMENSIONS:
        return "openai"
    if name in LOCAL_DIMENSIONS:
        return "local"
    return None

def _pin_to_corpus(model: str) -> None:
    """
    Adopts the model that embedded the stored corpus.

    This closes a deadlock that made retrieval permanently dead after a
    restart. `_openai_degraded` is in-process state, so a container that
    fell back to the local model — and therefore embedded its corpus
    locally — comes back up with the latch clear and `auto` selecting
    OpenAI again. `assert_matches_corpus` then runs BEFORE any OpenAI call
    and raises a 409, so the failure that would re-set the latch never
    happens: every query 409s forever, and the fallback that exists
    precisely for a dead key can never re-engage.

    Under `auto` the corpus wins, because `auto` means "pick the provider
    that works" and the only provider that can search these vectors is the
    one that wrote them. An explicit `openai` or `local` is an instruction
    and still gets the honest mismatch error instead.
    """
    global _corpus_pin
    with _degrade_lock:
        if _corpus_pin == model:
            return
        _corpus_pin = model
    logger.warning(
        "Adopting '%s' for retrieval: it is what embedded the stored chunks. "
        "Set EMBEDDING_PROVIDER explicitly to override, or re-ingest to change model.",
        model,
    )

def reset_degraded_state() -> None:
    """Clears the fallback latch and corpus pin. Used by tests, and after fixing a key."""
    global _openai_degraded, _corpus_pin, _embed_client
    with _degrade_lock:
        _openai_degraded = None
        _corpus_pin = None
        _embed_client = None

def active_provider() -> str:
    """
    'openai' or 'local'.

    EMBEDDING_PROVIDER=auto (the default) prefers OpenAI when a key is
    configured, because it is the better model and someone who set a key
    presumably wants it used — but demotes to local once a call has failed
    in a way that says the key itself is the problem (see
    `_note_openai_failure`).

    Explicit 'openai' or 'local' is honoured exactly as written. Switching
    providers against a database that already holds vectors is still never
    silent: the two models don't share a space, so `assert_matches_corpus`
    turns the mismatch into a sentence naming both models.
    """
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
    """For /health, so the UI can say which retrieval backend is live."""
    info = {"provider": active_provider(), "model": model_name(), "dimension": dimension()}
    if _openai_degraded:
        info["fell_back_from_openai"] = _openai_degraded[:300]
    if _corpus_pin:
        info["pinned_to_corpus_model"] = _corpus_pin
    return info

def _get_local_model():
    """
    Loads the sentence-transformers model once. ~130MB for bge-small, and
    several seconds to construct, so doing it per request would dominate
    the response time. Double-checked under a lock because uvicorn serves
    from a thread pool and two concurrent first-requests would otherwise
    both pay the load.
    """
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
        vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:
        raise EmbeddingUnavailable(f"Local embedding failed: {exc}") from exc
    return [v.tolist() for v in vectors]

_embed_client = None

def _get_embedding_client():
    """
    A client for the *embeddings* endpoint, which is not necessarily the
    same service as the chat endpoint.

    Built here rather than borrowed from `llm.get_client()` because that
    one now follows LLM_BASE_URL, and pointing an embeddings call at a
    chat-only gateway produces a 404 that reads like a dead key. Keeping
    the two clients separate is what allows the sensible mixed setup:
    prose from a cheap gateway, vectors from OpenAI or from the local model.
    """
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
            response = client.embeddings.create(model=config.EMBEDDING_MODEL, input=batch)
        except Exception as exc:
            _note_openai_failure(exc)
            raise EmbeddingUnavailable(f"Embedding request failed: {exc}") from exc
        out.extend(item.embedding for item in response.data)

    return out

def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embeds passages for storage. Batched by the backend as appropriate."""
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
    """
    Embeds one search query.

    Split from embed_documents because asymmetric models want the two
    sides encoded differently — see BGE_QUERY_PREFIX. For OpenAI the two
    paths are identical, which is exactly why doing this at the provider
    boundary rather than at each call site is worth it.
    """
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
    """Which model embedded the stored chunks, or None if there are none."""
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
    """
    Raises CorpusMismatch if the active model isn't the one that embedded
    the corpus.

    Without this the failure surfaces as
    `expected 384 dimensions, not 1536` from inside pgvector on a query
    the user thought was about Apple's risk factors. The fix is always the
    same — re-ingest, or switch EMBEDDING_PROVIDER back — so say that.
    """
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
