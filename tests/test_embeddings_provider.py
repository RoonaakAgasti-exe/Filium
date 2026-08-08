"""
Tests for embedding provider selection.

The interesting behaviour here is not "which model do we prefer" but what
happens when the preference and reality disagree — a key that is set but
out of credit, or a corpus embedded by the other model. Both were real
failure modes that took retrieval down completely, so both are pinned
here.

`active_provider` reads module-level latches, so every test resets them
via `reset_degraded_state` and restores the config it monkeypatched.
"""
import pytest

from backend import embeddings


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Each test starts with no degrade latch and no corpus pin."""
    embeddings.reset_degraded_state()
    monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "auto")
    monkeypatch.setattr(embeddings.config, "REMOTE_EMBEDDINGS_AVAILABLE", True)
    monkeypatch.setattr(embeddings.config, "EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(embeddings.config, "LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    yield
    embeddings.reset_degraded_state()


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
    """Answers `corpus_model`'s single SELECT with a canned model name."""

    def __init__(self, stored_model=None):
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
        embeddings._note_openai_failure(
            Exception("Error code: 429 - {'type': 'insufficient_quota'}")
        )
        assert embeddings.active_provider() == "local"
        assert "429" in embeddings.openai_degraded_reason()

    def test_a_transient_failure_does_not_demote(self):
        # A dropped connection says nothing about the key, and demoting on
        # one would strand a deployment on the weaker model for the life of
        # the process over a blip.
        embeddings._note_openai_failure(Exception("Connection reset by peer"))
        assert embeddings.active_provider() == "openai"

    def test_an_explicit_provider_is_honoured_over_a_failure(self, monkeypatch):
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openai")
        embeddings._note_openai_failure(Exception("insufficient_quota"))
        assert embeddings.active_provider() == "openai"


class TestCorpusAdoption:
    """
    The restart deadlock.

    `_openai_degraded` is in-process state. A container that fell back to
    the local model embedded its corpus locally, then came back up with the
    latch clear and `auto` selecting OpenAI again — so `assert_matches_corpus`
    raised a 409 BEFORE any OpenAI call, and the failure that would have
    re-set the latch could never happen. Every query 409'd forever.
    """

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
        # The configured local model is bge-small, but the corpus was built
        # with bge-base. Adopting only the *backend* would query 768-wide
        # vectors with a 384-wide model.
        embeddings.assert_matches_corpus(FakeConn("BAAI/bge-base-en-v1.5"))
        assert embeddings.model_name() == "BAAI/bge-base-en-v1.5"
        assert embeddings.dimension() == 768

    def test_an_explicit_provider_still_gets_the_mismatch_error(self, monkeypatch):
        # `auto` means "pick the one that works"; `openai` is an instruction
        # and deserves an honest failure rather than a silent switch.
        monkeypatch.setattr(embeddings.config, "EMBEDDING_PROVIDER", "openai")
        with pytest.raises(embeddings.CorpusMismatch, match="not comparable"):
            embeddings.assert_matches_corpus(FakeConn("BAAI/bge-small-en-v1.5"))

    def test_an_unknown_corpus_model_still_raises(self):
        # Adopting a model this build has no dimension for would push the
        # failure down into pgvector as a width error instead.
        with pytest.raises(embeddings.CorpusMismatch):
            embeddings.assert_matches_corpus(FakeConn("some-model-we-cannot-run"))
