import json
import pytest
import config
import embeddings
import llm

@pytest.fixture(autouse = True)
def _reset():
    llm.reset_degraded()
    llm.reset_client()
    embeddings.reset_degraded_state()
    yield
    llm.reset_degraded()
    llm.reset_client()
    embeddings.reset_degraded_state()

@pytest.fixture
def tokenrouter(monkeypatch):
    monkeypatch.setattr(config, "LLM_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(config, "LLM_MODEL", "moonshotai/kimi-k3-free")
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "")
    monkeypatch.setattr(config, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(config, "REMOTE_EMBEDDINGS_AVAILABLE", False)

class TestClientTargeting:
    def test_a_custom_base_url_is_where_requests_actually_go(self, tokenrouter):
        client = llm.get_client()
        assert "api.tokenrouter.com" in str(client.base_url)

    def test_an_empty_base_url_leaves_the_sdk_pointing_at_openai(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_BASE_URL", "")
        monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-a-real-key")
        monkeypatch.setattr(config, "LLM_ENABLED", True)
        assert "api.openai.com" in str(llm.get_client().base_url)

    def test_the_client_is_rebuilt_after_a_credential_change(self, tokenrouter, monkeypatch):
        assert "api.tokenrouter.com" in str(llm.get_client().base_url)
        monkeypatch.setattr(config, "LLM_BASE_URL", "")
        llm.reset_client()
        assert "api.openai.com" in str(llm.get_client().base_url)

class TestEmbeddingsDoNotFollowTheChatEndpoint:
    def test_a_chat_only_gateway_leaves_retrieval_on_the_local_model(self, tokenrouter, monkeypatch):
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "auto")
        assert embeddings.active_provider() == "local"

    def test_asking_for_a_remote_embedding_client_says_which_setting_is_missing(self, tokenrouter):
        with pytest.raises(embeddings.EmbeddingUnavailable, match = "EMBEDDING_BASE_URL"):
            embeddings._get_embedding_client()

    def test_naming_a_real_embedding_endpoint_re_enables_the_remote_path(self, tokenrouter, monkeypatch):
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "auto")
        monkeypatch.setattr(config, "EMBEDDING_BASE_URL", "https://api.openai.com/v1")
        monkeypatch.setattr(config, "EMBEDDING_API_KEY", "sk-test-not-a-real-key")
        monkeypatch.setattr(config, "REMOTE_EMBEDDINGS_AVAILABLE", True)
        assert embeddings.active_provider() == "openai"
        assert "api.openai.com" in str(embeddings._get_embedding_client().base_url)

class _FakeMessage:
    def __init__(self, content):
        self.content = content

class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)

class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]

def _fake_client(monkeypatch, responder):
    class _Completions:
        def create(self, **kwargs):
            return responder(**kwargs)

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()
    monkeypatch.setattr(llm, "get_client", lambda: _Client())

class TestJsonParsingSurvivesAConversationalModel:
    def test_a_clean_object_parses(self, monkeypatch):
        _fake_client(monkeypatch, lambda **kw: _FakeResponse('{"ticker":"NVDA"}'))
        assert llm.complete_json("x") == {"ticker": "NVDA"}

    def test_a_fenced_object_parses(self, monkeypatch):
        _fake_client(monkeypatch, lambda **kw: _FakeResponse('```json\n{"ticker": "NVDA"}\n```'))
        assert llm.complete_json("x") == {"ticker":"NVDA"}

    def test_an_object_buried_in_prose_parses(self, monkeypatch):
        _fake_client(monkeypatch, lambda **kw: _FakeResponse('Sure! Here is the rule:\n{"ticker": "NVDA", "rule_type": "sentiment_below"}\nHope that helps.'))
        assert llm.complete_json("x")["rule_type"] == "sentiment_below"

    def test_a_json_array_is_refused_rather_than_returned_as_a_list(self, monkeypatch):
        _fake_client(monkeypatch, lambda **kw: _FakeResponse('[1, 2, 3]'))
        with pytest.raises(llm.LLMUnavailable, match = "malformed JSON"):
            llm.complete_json("x")

    def test_unparseable_output_is_not_latched_as_a_dead_key(self, monkeypatch):
        _fake_client(monkeypatch, lambda **kw: _FakeResponse("I cannot help with that."))
        monkeypatch.setattr(config, "LLM_ENABLED", True)
        with pytest.raises(llm.LLMUnavailable):
            llm.complete_json("x")
        assert llm.degraded_reason() is None

class TestResponseFormatIsTreatedAsOptional:
    def test_a_provider_that_rejects_the_parameter_is_retried_without_it(self, monkeypatch):
        seen = []
        def responder(**kwargs):
            seen.append("response_format" in kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("Error code:400 - unsupported parameter:'response_format'")
            return _FakeResponse('{"ok": true}')
        _fake_client(monkeypatch, responder)
        assert llm.complete_json("x") == {"ok": True}
        assert seen == [True, False]

    def test_rejecting_the_parameter_does_not_disable_generation(self, monkeypatch):
        def responder(**kwargs):
            if "response_format" in kwargs:
                raise RuntimeError("Error code: 400 - unsupported parameter: response_format")
            return _FakeResponse('{"ok": true}')
        _fake_client(monkeypatch, responder)
        monkeypatch.setattr(config, "LLM_ENABLED", True)
        llm.complete_json("x")
        assert llm.degraded_reason() is None

    def test_an_unrelated_400_is_not_retried(self, monkeypatch):
        calls = []
        def responder(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("Error code: 400 - context length exceeded")
        _fake_client(monkeypatch, responder)
        with pytest.raises(llm.LLMUnavailable):
            llm.complete_json("x")
        assert len(calls) == 1

class TestGatewayCredentialErrorsLatch:
    def test_tokenrouters_invalid_token_wording_disables_generation(self, tokenrouter):
        llm._note_failure(RuntimeError('Error code: 401 - {"error":{"message":"Invalid token (request id: abc)"}}'))
        assert llm.is_configured() is False
        assert "Invalid token" in llm.degraded_reason()

    def test_an_ordinary_rate_limit_still_does_not_latch(self, tokenrouter):
        llm._note_failure(RuntimeError("Error code: 429 - rate_limit_exceeded, slow down"))
        assert llm.is_configured() is True