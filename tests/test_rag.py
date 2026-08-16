import pytest
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
    }
]

@pytest.fixture
def stub_pipeline(monkeypatch):
    monkeypatch.setattr(rag.embeddings, "assert_matches_corpus", lambda conn:None)
    monkeypatch.setattr(rag.llm, "embed_query", lambda text:[0.0, 1.0])
    monkeypatch.setattr(rag, "retrieve_chunks", lambda *a, **k:[dict(c) for c in CHUNKS])
    monkeypatch.setattr(rag, "rerank_chunks", lambda query, chunks, top_k:chunks[:top_k])

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
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:(_ for _ in ()).throw(llm.LLMUnavailable("no key")))
        result = rag.answer_query(None, "supply chain?", "AAPL")
        assert result["answer"].startswith(rag.EXTRACTIVE_PREAMBLE)

    def test_extractive_citations_resolve_to_real_sources(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:(_ for _ in ()).throw(llm.LLMUnavailable("no key")))
        result = rag.answer_query(None, "supply chain?", "AAPL")
        assert [s["marker"] for s in result["sources"]] == [1, 2]
        assert result["sources"][0]["chunk_id"] == 11
        assert result["sources"][0]["source_url"] == "https://sec.gov/aapl-10k"

    def test_peer_queries_degrade_the_same_way(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:(_ for _ in ()).throw(llm.LLMUnavailable("no key")))
        result = rag.answer_peer_query(None, "supply chain?", ["AAPL", "MSFT"])
        assert result["generated"] is False
        assert rag.EXTRACTIVE_PREAMBLE in result["answer"]

    def test_a_chunk_with_no_section_label_still_renders(self, monkeypatch):
        unlabelled = dict(CHUNKS[0], section_label = None)
        text = rag.extractive_answer([unlabelled])
        assert "unlabelled section" in text
        assert "[1]" in text

class TestAnsweringWithAnLLM:
    def test_marks_a_written_answer_as_generated(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:"Supply is concentrated in Asia [1].")
        result = rag.answer_query(None, "supply chain?", "AAPL")
        assert result["generated"] is True
        assert result["answer"] == "Supply is concentrated in Asia [1]."
        assert [s["marker"] for s in result["sources"]] == [1]

    def test_uncited_chunks_stay_out_of_sources_but_appear_in_all_sources(self, stub_pipeline, monkeypatch):
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:"Only this one [2].")
        result = rag.answer_query(None, "supply chain?", "AAPL")
        assert [s["marker"] for s in result["sources"]] == [2]
        assert [s["marker"] for s in result["all_sources"]] == [1, 2]

def _capture_local_embedding(monkeypatch) -> dict:
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
        called = {}

        def spy(text):
            called["text"] = text
            return [0.0, 1.0]
        monkeypatch.setattr(rag.llm, "embed_query", spy)
        monkeypatch.setattr(rag.llm, "complete", lambda *a, **k:"answer [1]")
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
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", True)
        assert embeddings.active_provider() == "local"

    def test_dimension_is_refused_for_an_unknown_model(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(embeddings.config, "LOCAL_EMBEDDING_MODEL", "some/unlisted-model")
        with pytest.raises(embeddings.EmbeddingUnavailable, match="Unknown embedding dimension"):
            embeddings.dimension()

    def test_an_unrecognised_setting_falls_back_rather_than_being_honoured(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openia")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)
        assert embeddings.active_provider() == "local"

    def test_health_reports_the_resolved_provider_not_the_raw_setting(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openia")
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)
        assert embeddings.config.integration_status()["embeddings"] == "local"
        assert embeddings.config.integration_status()["embeddings"] == \
            embeddings.active_provider()

class _CorpusCursor:
    def __init__(self, row, blow_up = False):
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
    def __init__(self, row = None, blow_up = False):
        self._row = row
        self._blow_up = blow_up
        self.rolled_back = False

    def cursor(self):
        return _CorpusCursor(self._row, self._blow_up)

    def rollback(self):
        self.rolled_back = True

class TestCorpusConsistency:
    def test_matching_model_passes(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda:"BAAI/bge-small-en-v1.5")
        embeddings.assert_matches_corpus(_CorpusConn(("BAAI/bge-small-en-v1.5",)))

    def test_empty_corpus_passes(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda:"BAAI/bge-small-en-v1.5")
        embeddings.assert_matches_corpus(_CorpusConn(None))

    def test_mismatch_names_both_models_and_the_way_out(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda:"BAAI/bge-small-en-v1.5")
        with pytest.raises(embeddings.CorpusMismatch) as exc:
            embeddings.assert_matches_corpus(_CorpusConn(("text-embedding-3-small",)))
        message = str(exc.value)
        assert "text-embedding-3-small" in message
        assert "BAAI/bge-small-en-v1.5" in message
        assert "re-ingest" in message

    def test_a_premigration_database_is_not_treated_as_a_mismatch(self, monkeypatch):
        monkeypatch.setattr(embeddings, "model_name", lambda:"BAAI/bge-small-en-v1.5")
        conn = _CorpusConn(blow_up = True)
        embeddings.assert_matches_corpus(conn)
        assert conn.rolled_back is True