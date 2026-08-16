import pytest
from backend import embeddings

@pytest.fixture(autouse = True)
def clean_state(monkeypatch):
    embeddings.reset_degraded_state()
    monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "auto")
    monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", True)
    monkeypatch.setattr(embeddings.config, "EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(embeddings.config, "LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    embeddings._corpus_pin = None
    yield
    embeddings.reset_degraded_state()
    embeddings._corpus_pin = None

class FakeCursor:
    def __init__(self, stored_model):
        self._stored = stored_model

    def execute(self, *args):
        pass

    def fetchone(self):
        return (self._stored,) if self._stored else None

    def close(self):
        pass

class FakeConn:
    def __init__(self, stored_model = None):
        self._stored = stored_model

    def cursor(self):
        return FakeCursor(self._stored)

    def rollback(self):
        pass

class TestAutoSelection:
    def test_a_configured_key_selects_openai(self):
        assert embeddings.active_provider() == "openai"

    def test_no_key_selects_local(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", False)
        assert embeddings.active_provider() == "local"

    def test_a_quota_failure_demotes_to_local(self):
        embeddings._note_openai_failure(Exception("Error code:429 - {'type':'insufficient_quota'}"))
        assert embeddings.active_provider() == "local"
        assert "429" in embeddings.openai_degraded_reason()

    def test_a_transient_failure_does_not_demote(self):
        embeddings._note_openai_failure(Exception("Connection reset by peer"))
        assert embeddings.active_provider() == "openai"

    def test_an_explicit_provider_is_honoured_over_a_failure(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openai")
        embeddings._note_openai_failure(Exception("insufficient_quota"))
        assert embeddings.active_provider() == "openai"

class TestCorpusAdoption:
    def test_an_empty_corpus_imposes_nothing(self):
        embeddings.assert_matches_corpus(FakeConn(None))
        assert embeddings.active_provider() == "openai"

    def test_a_matching_corpus_changes_nothing(self):
        embeddings.assert_matches_corpus(FakeConn("text-embedding-3-small"))
        assert embeddings.active_provider() == "openai"

    def test_a_locally_embedded_corpus_is_adopted_under_auto(self):
        embeddings.assert_matches_corpus(FakeConn("BAAI/bge-small-en-v1.5"))
        assert embeddings.active_provider() == "local"
        assert embeddings.model_name() == "BAAI/bge-small-en-v1.5"
        assert embeddings.dimension() == 384

    def test_adoption_is_reported_at_health(self):
        embeddings.assert_matches_corpus(FakeConn("BAAI/bge-small-en-v1.5"))
        assert embeddings.describe()["pinned_to_corpus_model"] == "BAAI/bge-small-en-v1.5"

    def test_adoption_honours_the_corpus_model_not_just_the_backend(self, monkeypatch):
        embeddings.assert_matches_corpus(FakeConn("BAAI/bge-base-en-v1.5"))
        assert embeddings.model_name() == "BAAI/bge-base-en-v1.5"
        assert embeddings.dimension() == 768

    def test_an_explicit_provider_still_gets_the_mismatch_error(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openai")
        with pytest.raises(embeddings.CorpusMismatch, match = "not comparable"):
            embeddings.assert_matches_corpus(FakeConn("BAAI/bge-small-en-v1.5"))

    def test_an_unknown_corpus_model_still_raises(self):
        with pytest.raises(embeddings.CorpusMismatch):
            embeddings.assert_matches_corpus(FakeConn("some-model-we-cannot-run"))