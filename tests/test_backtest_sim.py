"""Tests for backtest_sim.py pure functions."""
from backend.backtest_sim import simulate


class TestSimulate:
    def test_empty_series(self):
        result = simulate([], starting_cash=10000)
        assert result["days"] == 0
        assert "error" in result

    def test_single_day(self):
        series = [{"date": "2024-01-01", "close": 100.0}]
        result = simulate(series, starting_cash=10000)
        assert result["days"] == 1
        assert "error" in result

    def test_no_predictions(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": None, "confidence": None},
            {"date": "2024-01-02", "close": 101.0, "predicted_direction": None, "confidence": None},
        ]
        result = simulate(series, starting_cash=10000)
        assert result["days"] == 2
        assert result["days_with_prediction"] == 0
        assert result["days_in_market"] == 0
        assert result["final_value"] == 10000
        assert result["strategy_return"] == 0.0
        assert result["buy_hold_return"] > 0

    def test_correct_up_prediction(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": "up", "confidence": 0.8},
            {"date": "2024-01-02", "close": 105.0, "predicted_direction": "up", "confidence": 0.8},
        ]
        result = simulate(series, starting_cash=10000, confidence_threshold=0.5)
        assert result["days_in_market"] == 1
        assert result["final_value"] > 10000
        assert result["strategy_return"] > 0
        assert result["trade_count"] == 1
        assert result["winning_days"] == 1
        assert result["win_rate"] == 1.0

    def test_wrong_up_prediction(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": "up", "confidence": 0.8},
            {"date": "2024-01-02", "close": 95.0, "predicted_direction": "up", "confidence": 0.8},
        ]
        result = simulate(series, starting_cash=10000, confidence_threshold=0.5)
        assert result["days_in_market"] == 1
        assert result["final_value"] < 10000
        assert result["strategy_return"] < 0
        assert result["trade_count"] == 1
        assert result["winning_days"] == 0
        assert result["win_rate"] == 0.0

    def test_confidence_threshold(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": "up", "confidence": 0.3},
            {"date": "2024-01-02", "close": 105.0, "predicted_direction": "up", "confidence": 0.3},
        ]
        result = simulate(series, starting_cash=10000, confidence_threshold=0.5)
        assert result["days_in_market"] == 0
        assert result["final_value"] == 10000
        assert result["trade_count"] == 0

    def test_down_prediction_stays_out(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": "down", "confidence": 0.8},
            {"date": "2024-01-02", "close": 105.0, "predicted_direction": "down", "confidence": 0.8},
        ]
        result = simulate(series, starting_cash=10000, confidence_threshold=0.5)
        assert result["days_in_market"] == 0
        assert result["final_value"] == 10000

    def test_equity_curve(self):
        series = [
            {"date": "2024-01-01", "close": 100.0, "predicted_direction": "up", "confidence": 0.8},
            {"date": "2024-01-02", "close": 105.0, "predicted_direction": "up", "confidence": 0.8},
            {"date": "2024-01-03", "close": 110.0, "predicted_direction": "up", "confidence": 0.8},
        ]
        result = simulate(series, starting_cash=10000, confidence_threshold=0.5)
        assert len(result["equity_curve"]) == 2
        assert result["equity_curve"][0]["strategy_value"] == 10500
        assert result["equity_curve"][1]["strategy_value"] == 11000
        assert result["equity_curve"][0]["buy_hold_value"] == 10500
        assert result["equity_curve"][1]["buy_hold_value"] == 11000
