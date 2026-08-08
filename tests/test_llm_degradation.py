"""
Tests for llm.py's permanent-failure latch.

The behaviour under test is a latency fix, not a correctness one: every
LLM-backed path in this app already has a working fallback, so a dead key
produced correct answers slowly rather than wrong answers. What the latch
changes is that the SDK's retry ladder is paid once per process instead
of on every question, alert and trade.

The distinction that actually matters here is which failures latch. A
billing failure is permanent and should switch generation off; an
ordinary rate limit or a network blip is transient and must not, because
latching on those would disable prose for the life of the process over
something that would have succeeded on the next attempt.
"""

import pytest

import config
import llm


@pytest.fixture(autouse=True)
def _reset():
    llm.reset_degraded()
    yield
    llm.reset_degraded()


@pytest.fixture
def keyed(monkeypatch):
    """A config that looks like a real, configured OpenAI key."""
    monkeypatch.setattr(config, "LLM_ENABLED", True)
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-a-real-key")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    llm.reset_client()


PERMANENT = [
    "Error code: 429 - {'error': {'message': 'You have no credits remaining.', "
    "'type': 'insufficient_quota', 'code': 'credit_balance_exhausted'}}",
    "Error code: 401 - {'error': {'code': 'invalid_api_key'}}",
    "Error code: 429 - {'error': {'type': 'billing_not_active'}}",
    "Your account_deactivated flag is set",
]

TRANSIENT = [
    # A plain rate limit. Same 429 status as the quota error above, which
    # is exactly why the marker list matches on the error *type* and not
    # the status code.
    "Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-4o-mini', "
    "'type': 'requests', 'code': 'rate_limit_exceeded'}}",
    "Connection error.",
    "Request timed out.",
    "Error code: 500 - {'error': {'message': 'The server had an error'}}",
    "Error code: 503 - service unavailable",
]


class TestWhichFailuresLatch:
    @pytest.mark.parametrize("message", PERMANENT)
    def test_a_billing_or_credential_failure_switches_generation_off(self, message):
        llm._note_failure(RuntimeError(message))
        assert llm.degraded_reason() == message

    @pytest.mark.parametrize("message", TRANSIENT)
    def test_a_transient_failure_leaves_generation_on(self, message):
        llm._note_failure(RuntimeError(message))
        assert llm.degraded_reason() is None

    def test_the_first_reason_is_kept_not_the_latest(self):
        llm._note_failure(RuntimeError("insufficient_quota one"))
        llm._note_failure(RuntimeError("invalid_api_key two"))
        assert llm.degraded_reason() == "insufficient_quota one"


class TestIsConfigured:
    def test_reports_true_for_a_working_key(self, keyed):
        assert llm.is_configured() is True

    def test_reports_false_once_the_key_is_known_dead(self, keyed):
        llm._note_failure(RuntimeError("insufficient_quota"))
        assert llm.is_configured() is False

    def test_reports_false_with_no_key_at_all(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_ENABLED", False)
        assert llm.is_configured() is False

    def test_a_transient_failure_does_not_flip_it(self, keyed):
        llm._note_failure(RuntimeError("rate_limit_exceeded"))
        assert llm.is_configured() is True


class TestGetClient:
    def test_refuses_immediately_once_degraded(self, keyed):
        """
        The point of the whole exercise: no client is constructed, so no
        request goes out and no retry ladder is paid.
        """
        llm._note_failure(RuntimeError("credit_balance_exhausted"))

        with pytest.raises(llm.LLMUnavailable) as excinfo:
            llm.get_client()

        # The provider's own words, so the reason survives to the log line
        # and to /health rather than being flattened to "unavailable".
        assert "credit_balance_exhausted" in str(excinfo.value)

    def test_says_the_key_is_missing_when_it_is_missing(self, monkeypatch):
        monkeypatch.setattr(config, "LLM_ENABLED", False)
        # Asserted on the variable name rather than on prose: the message's
        # job is to tell the reader which setting to go and fill in.
        with pytest.raises(llm.LLMUnavailable, match="LLM_API_KEY"):
            llm.get_client()


class TestCompleteLatchesThroughThePublicApi:
    def _client_raising(self, monkeypatch, message):
        class _Completions:
            def create(self, **kwargs):
                raise RuntimeError(message)

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        monkeypatch.setattr(llm, "_client", _Client())

    def test_a_quota_failure_through_complete_disables_later_calls(self, keyed, monkeypatch):
        self._client_raising(monkeypatch, "Error code: 429 insufficient_quota")

        with pytest.raises(llm.LLMUnavailable):
            llm.complete("hello")

        assert llm.is_configured() is False

    def test_a_rate_limit_through_complete_leaves_it_enabled(self, keyed, monkeypatch):
        self._client_raising(monkeypatch, "Error code: 429 rate_limit_exceeded")

        with pytest.raises(llm.LLMUnavailable):
            llm.complete("hello")

        assert llm.is_configured() is True

    def test_try_complete_returns_none_rather_than_raising(self, keyed, monkeypatch):
        self._client_raising(monkeypatch, "Error code: 429 insufficient_quota")
        assert llm.try_complete("hello") is None

    def test_malformed_json_is_not_treated_as_a_dead_key(self, keyed, monkeypatch):
        """
        A bad body means the call *succeeded*. Latching on it would
        disable generation because the model once wrote something odd.
        """
        class _Message:
            content = "this is not json"

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        class _Completions:
            def create(self, **kwargs):
                return _Response()

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        monkeypatch.setattr(llm, "_client", _Client())

        with pytest.raises(llm.LLMUnavailable, match="malformed JSON"):
            llm.complete_json("hello")

        assert llm.is_configured() is True
