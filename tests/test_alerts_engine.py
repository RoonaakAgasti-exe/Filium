"""
Tests for alerts_engine rule evaluation.

`evaluate_rule` isn't a pure function over a context dict — every branch
reads the latest predictions, closes or sentiment straight out of the
database and returns a human-readable message (or None) rather than a
bool. So these drive it through a fake connection that answers each
lookup from canned rows.

Routing in the fake is by table name rather than exact SQL text: the
queries in alerts_engine are the only thing that distinguishes them, and
matching whole statements would turn any whitespace edit in the module
under test into a test failure.
"""
import pytest
from datetime import date, datetime, timedelta, timezone

from backend.alerts_engine import (
    RULE_DESCRIPTIONS,
    RULE_TYPES,
    _already_fired_today,
    evaluate_all_alerts,
    evaluate_rule,
    parse_alert_text,
)

# Column order of the SELECT in evaluate_all_alerts.
ALERT_COLUMNS = ("id", "user_id", "ticker", "rule_type", "threshold",
                 "natural_language", "last_fired_at")

FIRED_AT = datetime(2024, 3, 14, 6, 0, tzinfo=timezone.utc)


class FakeCursor:
    def __init__(self, db: dict):
        self._db = db
        self._rows: list = []
        self.description = None

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.description = None

        if "FROM alerts WHERE is_active" in sql:
            self.description = [(name,) for name in ALERT_COLUMNS]
            self._rows = [tuple(a.get(c) for c in ALERT_COLUMNS)
                          for a in self._db.get("alerts", [])]
        elif "FROM predictions" in sql:
            self._rows = self._db.get("predictions", [])
        elif "FROM price_history" in sql:
            self._rows = self._db.get("price_history", [])
        elif "FROM sentiment_scores" in sql:
            self._rows = self._db.get("sentiment_scores", [])
        elif sql.startswith("INSERT INTO alert_events"):
            self._db.setdefault("events", []).append(
                {"alert_id": params[0], "user_id": params[1],
                 "ticker": params[2], "message": params[3]}
            )
            self._rows = [(len(self._db["events"]), FIRED_AT)]
        elif sql.startswith("UPDATE alerts SET last_fired_at"):
            self._db.setdefault("stamped", []).append(params[1])
            self._rows = []
        elif "SELECT email FROM users" in sql:
            email = self._db.get("email")
            self._rows = [(email,)] if email else []
        elif sql.startswith("UPDATE alert_events SET emailed"):
            self._db.setdefault("emailed", []).append(params[0])
            self._rows = []
        else:
            raise AssertionError(f"Unexpected query: {sql}")

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def close(self):
        pass


class FakeConn:
    """
    `predictions` rows are (prediction_date, predicted_direction, confidence),
    `price_history` rows are (date, close), `sentiment_scores` rows are
    (date, score) — matching the SELECT column order in alerts_engine.
    Rows are supplied newest-first, as the ORDER BY ... DESC returns them.
    """

    def __init__(self, **tables):
        self.db = dict(tables)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


D1 = date(2024, 3, 14)
D0 = date(2024, 3, 13)


def alert(rule_type: str, threshold=None, ticker: str = "AAPL", **overrides) -> dict:
    row = {"id": 1, "user_id": 1, "ticker": ticker, "rule_type": rule_type,
           "threshold": threshold, "natural_language": None, "last_fired_at": None}
    row.update(overrides)
    return row


class TestRuleTypeRegistry:
    def test_every_rule_type_is_described(self):
        assert set(RULE_DESCRIPTIONS) == set(RULE_TYPES)

    def test_every_rule_type_is_handled(self):
        """
        A rule type that's advertised but falls through to the unknown-type
        branch would be selectable in the UI and then silently never fire.
        Each is given input that should make it produce a message.
        """
        conn = FakeConn(
            predictions=[(D1, "up", 0.91), (D0, "down", 0.72)],
            price_history=[(D1, 110.0), (D0, 100.0)],
            sentiment_scores=[(D1, 0.5)],
        )
        thresholds = {"prediction_flip": None, "price_move": 1.0,
                      "sentiment_below": 0.9, "sentiment_above": 0.1,
                      "confidence_above": 0.5}
        for rule_type in RULE_TYPES:
            message = evaluate_rule(conn, alert(rule_type, thresholds[rule_type]))
            assert message, f"{rule_type} produced no message"


class TestPredictionFlip:
    def test_fires_when_direction_changes(self):
        conn = FakeConn(predictions=[(D1, "up", 0.8), (D0, "down", 0.6)])
        message = evaluate_rule(conn, alert("prediction_flip"))
        assert message is not None
        assert "AAPL" in message
        assert "DOWN" in message and "UP" in message

    def test_silent_when_direction_holds(self):
        conn = FakeConn(predictions=[(D1, "up", 0.8), (D0, "up", 0.6)])
        assert evaluate_rule(conn, alert("prediction_flip")) is None

    def test_silent_with_only_one_prediction(self):
        # Nothing to compare against yet — a first-ever prediction is not a flip.
        conn = FakeConn(predictions=[(D1, "up", 0.8)])
        assert evaluate_rule(conn, alert("prediction_flip")) is None

    def test_silent_with_no_predictions(self):
        assert evaluate_rule(FakeConn(predictions=[]), alert("prediction_flip")) is None


class TestPriceMove:
    def test_fires_on_move_past_threshold(self):
        conn = FakeConn(price_history=[(D1, 105.0), (D0, 100.0)])
        message = evaluate_rule(conn, alert("price_move", 3.0))
        assert message is not None
        assert "up" in message and "5.00%" in message

    def test_fires_on_downward_move(self):
        conn = FakeConn(price_history=[(D1, 95.0), (D0, 100.0)])
        message = evaluate_rule(conn, alert("price_move", 3.0))
        assert message is not None
        assert "down" in message and "5.00%" in message

    def test_fires_exactly_at_threshold(self):
        # The rule reads "moves more than N%" as >=; a 5% move on a 5%
        # threshold should notify rather than sit one cent under the line.
        conn = FakeConn(price_history=[(D1, 105.0), (D0, 100.0)])
        assert evaluate_rule(conn, alert("price_move", 5.0)) is not None

    def test_silent_below_threshold(self):
        conn = FakeConn(price_history=[(D1, 101.0), (D0, 100.0)])
        assert evaluate_rule(conn, alert("price_move", 3.0)) is None

    def test_silent_with_one_close(self):
        conn = FakeConn(price_history=[(D1, 105.0)])
        assert evaluate_rule(conn, alert("price_move", 3.0)) is None

    def test_silent_when_previous_close_is_zero(self):
        # Guards the division; a zero close is bad data, not an infinite move.
        conn = FakeConn(price_history=[(D1, 105.0), (D0, 0.0)])
        assert evaluate_rule(conn, alert("price_move", 3.0)) is None

    def test_silent_without_threshold(self):
        conn = FakeConn(price_history=[(D1, 105.0), (D0, 100.0)])
        assert evaluate_rule(conn, alert("price_move", None)) is None


class TestSentiment:
    def test_below_fires(self):
        conn = FakeConn(sentiment_scores=[(D1, -0.45)])
        message = evaluate_rule(conn, alert("sentiment_below", -0.2))
        assert message is not None
        assert "fell below" in message

    def test_below_silent_when_above(self):
        conn = FakeConn(sentiment_scores=[(D1, 0.3)])
        assert evaluate_rule(conn, alert("sentiment_below", -0.2)) is None

    def test_above_fires(self):
        conn = FakeConn(sentiment_scores=[(D1, 0.6)])
        message = evaluate_rule(conn, alert("sentiment_above", 0.2))
        assert message is not None
        assert "rose above" in message

    def test_above_silent_when_below(self):
        conn = FakeConn(sentiment_scores=[(D1, 0.1)])
        assert evaluate_rule(conn, alert("sentiment_above", 0.2)) is None

    def test_silent_with_no_sentiment_rows(self):
        conn = FakeConn(sentiment_scores=[])
        assert evaluate_rule(conn, alert("sentiment_below", -0.2)) is None

    def test_boundary_is_strict(self):
        # Score exactly at the threshold hasn't crossed it in either direction.
        conn = FakeConn(sentiment_scores=[(D1, -0.2)])
        assert evaluate_rule(conn, alert("sentiment_below", -0.2)) is None
        assert evaluate_rule(conn, alert("sentiment_above", -0.2)) is None


class TestConfidenceAbove:
    def test_fires_above_threshold(self):
        conn = FakeConn(predictions=[(D1, "up", 0.85)])
        message = evaluate_rule(conn, alert("confidence_above", 0.7))
        assert message is not None
        assert "85%" in message

    def test_fires_exactly_at_threshold(self):
        conn = FakeConn(predictions=[(D1, "up", 0.7)])
        assert evaluate_rule(conn, alert("confidence_above", 0.7)) is not None

    def test_silent_below_threshold(self):
        conn = FakeConn(predictions=[(D1, "up", 0.55)])
        assert evaluate_rule(conn, alert("confidence_above", 0.7)) is None

    def test_silent_with_no_predictions(self):
        assert evaluate_rule(FakeConn(predictions=[]), alert("confidence_above", 0.7)) is None


class TestUnknownRuleType:
    def test_returns_none_rather_than_raising(self):
        # evaluate_all_alerts catches exceptions per alert, but an unknown
        # type is a data problem, not an error — it must not fire either.
        conn = FakeConn()
        assert evaluate_rule(conn, alert("not_a_real_rule", 1.0)) is None


class TestAlreadyFiredToday:
    """
    The daily-suppression check. Without it a standing condition like
    "sentiment below -0.2" re-notifies on every scheduler run until the
    sentiment happens to move.
    """

    def test_never_fired(self):
        assert _already_fired_today({"last_fired_at": None}, date(2024, 3, 14)) is False

    def test_missing_key_counts_as_never_fired(self):
        assert _already_fired_today({}, date(2024, 3, 14)) is False

    def test_fired_earlier_today(self):
        today = date(2024, 3, 14)
        fired = datetime(2024, 3, 14, 6, 0, tzinfo=timezone.utc)
        assert _already_fired_today({"last_fired_at": fired}, today) is True

    def test_fired_yesterday(self):
        today = date(2024, 3, 14)
        fired = datetime(2024, 3, 13, 23, 59, tzinfo=timezone.utc)
        assert _already_fired_today({"last_fired_at": fired}, today) is False

    def test_accepts_plain_date(self):
        today = date(2024, 3, 14)
        assert _already_fired_today({"last_fired_at": today}, today) is True
        assert _already_fired_today({"last_fired_at": today - timedelta(days=1)}, today) is False


class TestEvaluateAllAlerts:
    """
    The entry point the nightly scheduler and the app's "check now" button
    both call. Email is switched off here so these assert on what gets
    recorded, not on SMTP.
    """

    def test_records_an_event_when_a_rule_fires(self):
        conn = FakeConn(
            alerts=[alert("price_move", 3.0)],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        fired = evaluate_all_alerts(conn, send_email=False)

        assert len(fired) == 1
        assert fired[0]["ticker"] == "AAPL"
        assert "5.00%" in fired[0]["message"]
        assert len(conn.db["events"]) == 1
        # last_fired_at must be stamped, or the suppression below never engages.
        assert conn.db["stamped"] == [1]

    def test_records_nothing_when_no_rule_fires(self):
        conn = FakeConn(
            alerts=[alert("price_move", 20.0)],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        assert evaluate_all_alerts(conn, send_email=False) == []
        assert "events" not in conn.db

    def test_suppressed_after_firing_today(self):
        conn = FakeConn(
            alerts=[alert("price_move", 3.0,
                          last_fired_at=datetime.now(timezone.utc))],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        assert evaluate_all_alerts(conn, send_email=False) == []

    def test_not_suppressed_after_firing_yesterday(self):
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        conn = FakeConn(
            alerts=[alert("price_move", 3.0, last_fired_at=yesterday)],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        assert len(evaluate_all_alerts(conn, send_email=False)) == 1

    def test_force_overrides_suppression(self):
        conn = FakeConn(
            alerts=[alert("price_move", 3.0,
                          last_fired_at=datetime.now(timezone.utc))],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        assert len(evaluate_all_alerts(conn, force=True, send_email=False)) == 1

    def test_one_broken_rule_does_not_stop_the_others(self):
        # A non-numeric confidence blows up float() inside the predictions
        # lookup. The scheduler runs every user's alerts in one pass, so one
        # bad row must not silence everyone else's notifications.
        conn = FakeConn(
            alerts=[
                alert("confidence_above", 0.5, ticker="BAD"),
                alert("price_move", 3.0, ticker="AAPL", id=2),
            ],
            predictions=[(D1, "up", "not-a-number")],
            price_history=[(D1, 105.0), (D0, 100.0)],
        )
        fired = evaluate_all_alerts(conn, send_email=False)

        assert len(fired) == 1
        assert fired[0]["ticker"] == "AAPL"
        assert conn.rollbacks == 1


class TestParseAlertText:
    """
    The keyword fallback used when no LLM is available.

    /alerts/natural used to 503 outright without OPENAI_API_KEY, which
    made the PDF's "custom alerts via natural language" feature absent on
    every keyless deployment — and on the much more common one whose key
    has run out of credit. The rule vocabulary is five fixed shapes, so
    matching on them directly loses tolerance for odd phrasing, not
    capability.
    """

    def test_the_pdf_example_parses(self):
        # The exact phrasing the PDF gives as the worked example.
        rule = parse_alert_text("notify me if NVDA's sentiment turns negative")
        assert rule["ticker"] == "NVDA"
        assert rule["rule_type"] == "sentiment_below"
        assert rule["threshold"] == 0.0

    def test_a_dollar_prefixed_ticker_is_recognised(self):
        assert parse_alert_text("tell me when $aapl's prediction flips")["ticker"] == "AAPL"

    def test_prediction_flip_needs_no_threshold(self):
        rule = parse_alert_text("alert me when TSLA changes direction")
        assert rule["rule_type"] == "prediction_flip"
        assert rule["threshold"] is None

    def test_a_percentage_becomes_a_price_move_threshold(self):
        rule = parse_alert_text("alert me if TSLA moves more than 5%")
        assert rule["rule_type"] == "price_move"
        assert rule["threshold"] == 5.0

    def test_a_price_move_threshold_is_always_positive(self):
        # "drops 3%" is a 3% move, not a -3% one; evaluate_rule compares
        # against abs(change), so a negative threshold would fire on
        # literally every trading day.
        assert parse_alert_text("tell me if GOOGL drops 3 percent")["threshold"] == 3.0

    def test_a_confidence_percentage_is_converted_to_a_fraction(self):
        # confidence_above is compared against `confidence`, which is 0-1.
        # Storing 80 rather than 0.8 makes the rule unfireable.
        rule = parse_alert_text("notify me when MSFT confidence is above 80%")
        assert rule["rule_type"] == "confidence_above"
        assert rule["threshold"] == 0.8

    def test_an_explicit_sentiment_level_is_kept_on_its_own_scale(self):
        rule = parse_alert_text("let me know if AMZN sentiment rises above 0.3")
        assert rule["rule_type"] == "sentiment_above"
        assert rule["threshold"] == 0.3

    def test_a_common_uppercase_word_is_not_mistaken_for_a_ticker(self):
        # "IF" is the first all-caps token in this sentence. Reading it as a
        # ticker would create a rule that silently watches the wrong thing,
        # which is worse than refusing to create one at all.
        assert parse_alert_text("notify me IF NVDA drops 4%")["ticker"] == "NVDA"

    def test_a_request_with_no_ticker_is_refused(self):
        with pytest.raises(ValueError, match="ticker"):
            parse_alert_text("notify me if sentiment turns negative")

    def test_a_request_that_maps_to_no_rule_is_refused(self):
        with pytest.raises(ValueError, match="supported alert"):
            parse_alert_text("do something clever with QQQ")

    def test_every_rule_type_it_returns_is_one_the_engine_knows(self):
        texts = [
            "NVDA sentiment turns negative",
            "AAPL prediction flips",
            "TSLA moves more than 5%",
            "MSFT confidence above 80%",
            "AMZN sentiment rises above 0.3",
        ]
        assert {parse_alert_text(t)["rule_type"] for t in texts} <= set(RULE_TYPES)
