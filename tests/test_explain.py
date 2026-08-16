from backend.explain import template_explanation

PREDICTION = {"id":7, "direction":"up", "confidence":0.77, "prediction_date":"2026-08-02", "target_date":"2026-08-03", "model":"augmented"}
SENTIMENT = {"date":"2026-08-01", "score":-0.09, "article_count":12}
EMPTY = {"prediction":None, "sentiment":None, "recent_close":None}

def context(prediction = None, sentiment = None):
    return {"prediction":prediction, "sentiment":sentiment, "recent_close":None}

class TestTradeAndSignalAgreement:
    def test_a_buy_on_an_up_signal_follows_it(self):
        text = template_explanation("buy", "AAPL", 2, 312.41, True, context(PREDICTION))
        assert "follows the signal" in text
        assert "runs against" not in text

    def test_a_buy_on_a_down_signal_says_it_runs_against_it(self):
        down = {**PREDICTION, "direction": "down"}
        text = template_explanation("buy", "AAPL", 2, 312.41, True, context(down))
        assert "runs against the signal" in text

    def test_a_sell_on_a_down_signal_follows_it(self):
        down = {**PREDICTION, "direction": "down"}
        text = template_explanation("sell", "AAPL", 2, 312.41, False, context(down))
        assert "follows the signal" in text

    def test_a_sell_on_an_up_signal_says_it_runs_against_it(self):
        text = template_explanation("sell", "AAPL", 2, 312.41, False, context(PREDICTION))
        assert "runs against the signal" in text

class TestContent:
    def test_it_names_the_model_direction_and_confidence(self):
        text = template_explanation("buy", "AAPL", 2, 312.41, True, context(PREDICTION))
        assert "augmented" in text
        assert "UP" in text
        assert "77%" in text

    def test_it_reports_the_action_shares_and_price(self):
        text = template_explanation("buy", "AAPL", 2.5, 312.41, False, context(PREDICTION))
        assert text.startswith("Bought 2.5 share(s) of AAPL at $312.41.")

    def test_a_sell_reads_as_sold(self):
        text = template_explanation("sell", "MSFT", 1, 500.0, False, EMPTY)
        assert text.startswith("Sold 1 share(s) of MSFT at $500.00.")

    def test_negative_sentiment_is_described_as_negative(self):
        text = template_explanation("buy", "AAPL", 2, 312.41, True, context(PREDICTION, SENTIMENT))
        assert "-0.09" in text and "negative" in text
        assert "12 article(s)" in text

    def test_a_near_zero_score_is_neutral_not_negative(self):
        flat = {**SENTIMENT, "score": 0.01}
        text = template_explanation("buy", "AAPL", 2, 312.41, True, context(PREDICTION, flat))
        assert "roughly neutral" in text

class TestMissingData:
    def test_a_missing_prediction_is_stated_not_implied(self):
        text = template_explanation("buy", "AAPL", 2, 312.41, False, EMPTY)
        assert "No model prediction exists" in text
        assert "follows the signal" not in text

    def test_missing_sentiment_is_stated(self):
        text = template_explanation("buy", "AAPL", 2, 312.41, False, context(PREDICTION))
        assert "No news sentiment has been scored" in text

    def test_it_produces_text_even_with_no_context_at_all(self):
        assert len(template_explanation("buy", "AAPL", 1, 100.0, False, EMPTY)) > 40