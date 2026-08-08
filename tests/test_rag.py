"""
Tests for the keyless retrieval path in rag.py and embeddings.py.

The promise being pinned down here is that retrieval and generation fail
independently. A deployment with no OpenAI key can still embed a corpus
and find the right passage in a 200-page 10-K — it just can't write prose
about it — so the chat feature degrades to quoting its evidence instead
of returning an error. These tests exist because that fallback is easy to
regress silently: every call site has to route through generate_answer,
and a single stray llm.complete() puts the 503 back.

Nothing here loads a model or touches a database. The embedding provider,
the reranker and the LLM are all stubbed at their module boundaries.
"""
import pytest

# Bare imports, not `backend.rag` — see the note in test_routers.py. rag.py
# catches `llm.LLMUnavailable` from the bare-named module, so an exception
# raised from `backend.llm` is a different class object and sails straight
# through the fallback this file exists to test.
import embeddings
import llm
import rag


CHUNKS = [
    {
        "id": 11,
        "chunk_id": 11,
        "chunk_text": "Our supply chain is concentrated in a small number of "
                      "manufacturing partners located primarily in Asia.",
        "section_label": "Item 1A. Risk Factors",
        "ticker": "AAPL",
        "filing_type": "10-K",
        "filing_date": "2025-11-01",
        "source_url": "https://sec.gov/aapl-10k",
        "filing_id": 3,
        "distance": 0.11,
    },
    {
        "id": 12,
        "chunk_id": 12,
        "chunk_text": "A disruption at any single supplier could materially "
                      "affect our ability to meet demand.",
        "section_label": "Item 1A. Risk Factors",
        "ticker": "AAPL",
        "filing_type": "10-K",
        "filing_date": "2025-11-01",
        "source_url": "https://sec.gov/aapl-10k",
        "filing_id": 3,
        "distance": 0.19,
    },
]


@pytest.fixture
def stub_pipeline(monkeypatch):
    """
    Stubs everything below the answer logic: the corpus check, the query
    embedding, the vector search and the cross-encoder. The reranker in
    particular must be stubbed — the real one downloads ~90MB on first
    call, which a unit test has no business doing.
    """
    monkeypatch.setattr(rag.embeddings, "assert_matches_corpus", lambda conn: None)
    monkeypatch.setattr(rag.llm, "embed_query", lambda text: [0.0, 1.0])
    monkeypatch.setattr(rag, "retrieve_chunks",
                        lambda *a, **k: [dict(c) for c in CHUNKS])
    monkeypatch.setattr(rag, "rerank_chunks", lambda query, chunks, top_k: chunks[:top_k])


class TestAnsweringWithoutAnLLM:
    def test_returns_the_retrieved_passages_instead_of_raising(self, stub_pipeline, monkeypatch):
        def no_llm(*args, **kwargs):
            raise llm.LLMUnavailable("OpenAI is not configured")

        monkeypatch.setattr(rag.llm, "complete", no_llm)

        result = rag.answer_query(None, "What are the supply chain risks?", "AAPL")

        assert result["generated"] is False
        assert rag.EXTRACTIVE_PREAMBLE in result["answer"]
        assert "manufacturing partners" in result["answer"]

    def test_the_preamble_travels_in_the_answer_text_itself(self, stub_pipeline, monkeypatch):
        # query_history stores response_text and nothing else. If the
        # disclaimer lived only in the `generated` flag, a stored
        # extractive answer would read back later as a model's words.
        monkeypatch.setattr(rag.llm, "complete",
                            lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("no key")))

        result = rag.answer_query(None, "supply chain?", "AAPL")

        assert result["answer"].startswith(rag.EXTRACTIVE_PREAMBLE)

    def test_extractive_citations_resolve_to_real_sources(self, stub_pipeline, monkeypatch):
        # The [N] markers are the same ones the UI resolves to highlighted
        # sources, so they have to survive the fallback, not just the
        # generated path.
        monkeypatch.setattr(rag.llm, "complete",
                            lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("no key")))

        result = rag.answer_query(None, "supply chain?", "AAPL")

        assert [s["marker"] for s in result["sources"]] == [1, 2]
        assert result["sources"][0]["chunk_id"] == 11
        assert result["sources"][0]["source_url"] == "https://sec.gov/aapl-10k"

    def test_peer_queries_degrade_the_same_way(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete",
                            lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("no key")))

        result = rag.answer_peer_query(None, "supply chain?", ["AAPL", "MSFT"])

        assert result["generated"] is False
        assert rag.EXTRACTIVE_PREAMBLE in result["answer"]

    def test_a_chunk_with_no_section_label_still_renders(self, monkeypatch):
        unlabelled = dict(CHUNKS[0], section_label=None)

        text = rag.extractive_answer([unlabelled])

        assert "unlabelled section" in text
        assert "[1]" in text


class TestAnsweringWithAnLLM:
    def test_marks_a_written_answer_as_generated(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete",
                            lambda *a, **k: "Supply is concentrated in Asia [1].")

        result = rag.answer_query(None, "supply chain?", "AAPL")

        assert result["generated"] is True
        assert result["answer"] == "Supply is concentrated in Asia [1]."
        assert [s["marker"] for s in result["sources"]] == [1]

    def test_uncited_chunks_stay_out_of_sources_but_appear_in_all_sources(
            self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k: "Only this one [2].")

        result = rag.answer_query(None, "supply chain?", "AAPL")

        assert [s["marker"] for s in result["sources"]] == [2]
        assert [s["marker"] for s in result["all_sources"]] == [1, 2]


def _capture_local_embedding(monkeypatch) -> dict:
    """
    Pins the local provider and records exactly what text reaches the
    model, without loading one. Returns a dict that gains a "texts" key
    when the encoder is called.
    """
    captured: dict = {}

    def fake_embed_local(texts):
        captured["texts"] = texts
        return [[0.0] for _ in texts]

    monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "local")
    monkeypatch.setattr(embeddings.config, "LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(embeddings, "_embed_local", fake_embed_local)
    return captured


class TestQueriesAreEmbeddedAsQueries:
    def test_retrieval_uses_embed_query_not_embed_documents(self, stub_pipeline, monkeypatch):
        """
        bge-* models want an instruction prefix on the query side only.
        Embedding a question as though it were a passage doesn't raise —
        it just quietly retrieves worse — so the call itself is asserted.
        """
        called = {}

        def spy(text):
            called["text"] = text
            return [0.0, 1.0]

        monkeypatch.setattr(rag.llm, "embed_query", spy)
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k: "answer [1]")

        rag.answer_query(None, "What are the supply chain risks?", "AAPL")

        assert called["text"] == "What are the supply chain risks?"

    def test_bge_query_prefix_is_applied_for_local_models(self, monkeypatch):
        captured = _capture_local_embedding(monkeypatch)

        embeddings.embed_query("supply chain risk")

        assert captured["texts"] == [embeddings.BGE_QUERY_PREFIX + "supply chain risk"]

    def test_documents_never_get_the_query_prefix(self, monkeypatch):
        captured = _capture_local_embedding(monkeypatch)

        embeddings.embed_documents(["a passage from a filing"])

        assert captured["texts"] == ["a passage from a filing"]


class TestProviderSelection:
    def test_local_is_used_when_no_openai_key_is_set(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "auto")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)

        assert embeddings.active_provider() == "local"

    def test_openai_wins_under_auto_when_a_key_exists(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "auto")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", True)

        assert embeddings.active_provider() == "openai"

    def test_an_explicit_setting_overrides_the_key(self, monkeypatch):
        # Someone with a key who wants the local model must be able to say
        # so — otherwise a corpus embedded locally can never be queried on
        # a machine that happens to have OPENAI_API_KEY exported.
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", True)

        assert embeddings.active_provider() == "local"

    def test_dimension_is_refused_for_an_unknown_model(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(embeddings.config, "LOCAL_EMBEDDING_MODEL", "some/unlisted-model")

        with pytest.raises(embeddings.EmbeddingUnavailable, match="Unknown embedding dimension"):
            embeddings.dimension()

    def test_an_unrecognised_setting_falls_back_rather_than_being_honoured(
            self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openia")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)

        assert embeddings.active_provider() == "local"

    def test_health_reports_the_resolved_provider_not_the_raw_setting(
            self, monkeypatch):
        # These two used to resolve EMBEDDING_PROVIDER independently, so a
        # typo'd value was echoed verbatim by /health while retrieval quietly
        # ran the local model — the one field you'd check to find the typo
        # was the field repeating it back to you.
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openia")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)

        assert embeddings.config.integration_status()["embeddings"] == "local"
        assert embeddings.config.integration_status()["embeddings"] == \
            embeddings.active_provider()


class _CorpusCursor:
    def __init__(self, row, blow_up=False):
        self._row = row
        self._blow_up = blow_up

    def execute(self, *args, **kwargs):
        if self._blow_up:
            raise RuntimeError('column "embedding_model" does not exist')

    def fetchone(self):
        return self._row

    def close(self):
        pass


class _CorpusConn:
    def __init__(self, row=None, blow_up=False):
        self._row = row
        self._blow_up = blow_up
        self.rolled_back = False

    def cursor(self):
        return _CorpusCursor(self._row, self._blow_up)

    def rollback(self):
        self.rolled_back = True


class TestCorpusConsistency:
    def test_matching_model_passes(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda: "BAAI/bge-small-en-v1.5")

        embeddings.assert_matches_corpus(_CorpusConn(("BAAI/bge-small-en-v1.5",)))

    def test_empty_corpus_passes(self, monkeypatch):
        # Nothing stored yet means nothing to be inconsistent with — this
        # is the fresh-install path and must not be blocked.
        monkeypatch.setattr(embeddings, "model_name", lambda: "BAAI/bge-small-en-v1.5")

        embeddings.assert_matches_corpus(_CorpusConn(None))

    def test_mismatch_names_both_models_and_the_way_out(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda: "BAAI/bge-small-en-v1.5")

        with pytest.raises(embeddings.CorpusMismatch) as exc:
            embeddings.assert_matches_corpus(_CorpusConn(("text-embedding-3-small",)))

        message = str(exc.value)
        assert "text-embedding-3-small" in message
        assert "BAAI/bge-small-en-v1.5" in message
        assert "re-ingest" in message

    def test_a_premigration_database_is_not_treated_as_a_mismatch(self, monkeypatch):
        # The column only exists after migration 004. Failing here would
        # break /health and every query on a database that is merely old.
        monkeypatch.setattr(embeddings, "model_name", lambda: "BAAI/bge-small-en-v1.5")
        conn = _CorpusConn(blow_up=True)

        embeddings.assert_matches_corpus(conn)

        # The failed SELECT must be rolled back or the connection stays
        # poisoned for everything that follows on it.
        assert conn.rolled_back is True
