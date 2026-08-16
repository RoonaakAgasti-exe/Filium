import math
from backend.analytics import (_stdev, daily_returns, sharpe_ratio, max_drawdown, trade_stats, sector_exposure, calibration_curve)

class TestDailyReturns:
    def test_empty(self):
        assert daily_returns([]) == []

    def test_single_value(self):
        assert daily_returns([100]) == []

    def test_two_values(self):
        result = daily_returns([100, 101])
        assert len(result) == 1
        assert abs(result[0] - 0.01) < 0.0001

    def test_multiple_values(self):
        result = daily_returns([100, 101, 100, 102])
        assert len(result) == 3
        assert abs(result[0] - 0.01) < 0.0001
        assert abs(result[1] - (-0.0099)) < 0.0001
        assert abs(result[2] - 0.02) < 0.0001

class TestSharpeRatio:
    def test_empty_returns(self):
        assert sharpe_ratio([]) is None

    def test_single_return(self):
        assert sharpe_ratio([0.01]) is None

    def test_constant_returns(self):
        assert sharpe_ratio([0.01, 0.01, 0.01]) is None

    def test_positive_sharpe(self):
        returns = [0.01, 0.02, -0.005, 0.015, 0.01]
        result = sharpe_ratio(returns)
        assert result is not None
        assert result > 0

    def test_annualization(self):
        returns = [0.011, -0.009, 0.011, -0.009] * 10
        n = len(returns)
        result = sharpe_ratio(returns)
        expected = (0.001 / (0.01 * math.sqrt(n / (n - 1)))) * math.sqrt(252)
        assert abs(result - expected) < 1e-9

    def test_risk_free_rate_lowers_sharpe(self):
        returns = [0.01, 0.02, -0.005, 0.015, 0.01]
        assert sharpe_ratio(returns, risk_free_rate = 0.05) < sharpe_ratio(returns)

class TestMaxDrawdown:
    def test_empty(self):
        assert max_drawdown([]) is None

    def test_single_value(self):
        assert max_drawdown([100]) is None

    def test_monotonic_increase(self):
        result = max_drawdown([100, 101, 102, 103])
        assert result["max_drawdown"] == 0.0

    def test_simple_drawdown(self):
        values = [100, 120, 90, 110]
        result = max_drawdown(values)
        assert abs(result["max_drawdown"] - (-0.25)) < 0.001

    def test_multiple_drawdowns(self):
        values = [100, 120, 90, 130, 80, 100]
        result = max_drawdown(values)
        assert abs(result["max_drawdown"] - (-50 / 130)) < 0.001

class TestTradeStats:
    def test_empty(self):
        result = trade_stats([])
        assert result["closed_trades"] == 0
        assert result["win_rate"] is None

    def test_all_wins(self):
        sells = [{"realized_pl":100}, {"realized_pl":200}]
        result = trade_stats(sells)
        assert result["closed_trades"] == 2
        assert result["wins"] == 2
        assert result["losses"] == 0
        assert result["win_rate"] == 1.0
        assert result["profit_factor"] is None

    def test_mixed(self):
        sells = [{"realized_pl":100}, {"realized_pl":-50}, {"realized_pl":200}]
        result = trade_stats(sells)
        assert result["closed_trades"] == 3
        assert result["wins"] == 2
        assert result["losses"] == 1
        assert abs(result["win_rate"] - 2/3) < 0.001
        assert result["profit_factor"] == 6.0

class TestSectorExposure:
    def test_empty(self):
        assert sector_exposure([]) == []

    def test_single_holding(self):
        holdings = [{"ticker":"AAPL", "market_value":10000, "sector":"Technology"}]
        result = sector_exposure(holdings)
        assert len(result) == 1
        assert result[0]["sector"] == "Technology"
        assert result[0]["weight"] == 1.0

    def test_multiple_sectors(self):
        holdings = [{"ticker":"AAPL", "market_value":6000, "sector":"Technology"}, {"ticker":"JNJ", "market_value":4000, "sector":"Healthcare"}]
        result = sector_exposure(holdings)
        assert len(result) == 2
        tech = next(r for r in result if r["sector"] == "Technology")
        health = next(r for r in result if r["sector"] == "Healthcare")
        assert abs(tech["weight"] - 0.6) < 0.001
        assert abs(health["weight"] - 0.4) < 0.001

    def test_missing_sector(self):
        holdings = [{"ticker":"XYZ", "market_value":1000}]
        result = sector_exposure(holdings)
        assert result[0]["sector"] == "Unclassified"

    def test_missing_market_value_skipped(self):
        holdings = [{"ticker":"AAPL", "market_value":10000, "sector":"Technology"}, {"ticker":"XYZ", "sector":"Unknown"}]
        result = sector_exposure(holdings)
        assert len(result) == 1
        assert result[0]["sector"] == "Technology"

class TestCalibrationCurve:
    def test_empty(self):
        result = calibration_curve([])
        assert len(result["bins"]) == 5
        assert all(b["count"] == 0 for b in result["bins"])
        assert result["num_resolved"] == 0
        assert result["expected_calibration_error"] is None
        assert result["brier_score"] is None

    def test_rows_without_prob_up_are_skipped(self):
        predictions = [{"prob_up":None, "actual_direction":"up"}, {"prob_up":0.8, "actual_direction":"up"}]
        assert calibration_curve(predictions)["num_resolved"] == 1

    def test_unresolved_rows_are_skipped(self):
        predictions = [{"prob_up":0.8, "actual_direction":None}, {"prob_up":0.8, "actual_direction":"up"}]
        assert calibration_curve(predictions)["num_resolved"] == 1

    def test_prob_up_of_one_lands_in_last_bin(self):
        result = calibration_curve([{"prob_up":1.0, "actual_direction":"up"}], num_bins = 5)
        assert result["num_resolved"] == 1
        assert result["bins"][-1]["count"] == 1

    def test_perfect_calibration(self):
        predictions = [{"prob_up":0.5, "actual_direction":"up" if i % 2 == 0 else "down"} for i in range(10)]
        result = calibration_curve(predictions, num_bins = 2)
        assert result["expected_calibration_error"] < 0.01

    def test_overconfident(self):
        predictions = [{"prob_up":0.9, "actual_direction":"up" if i % 2 == 0 else "down"} for i in range(10)]
        result = calibration_curve(predictions, num_bins = 2)
        assert result["expected_calibration_error"] > 0.3

    def test_brier_score(self):
        predictions = [{"prob_up":1.0, "actual_direction":"up"}, {"prob_up":0.0, "actual_direction":"down"}]
        result = calibration_curve(predictions)
        assert result["brier_score"] == 0.0

    def test_brier_score_worst_case(self):
        predictions = [{"prob_up":1.0, "actual_direction":"down"}, {"prob_up":0.0, "actual_direction":"up"}]
        assert calibration_curve(predictions)["brier_score"] == 1.0

class TestStdev:
    def test_too_few_values(self):
        assert _stdev([]) == 0.0
        assert _stdev([0.01]) == 0.0

    def test_constant_series_has_no_spread(self):
        assert _stdev([0.01, 0.01, 0.01]) == 0.0

    def test_sample_stdev(self):
        assert abs(_stdev([1, 2, 3, 4]) - math.sqrt(5 / 3)) < 1e-12

    def test_annualized_volatility(self):
        returns = [0.011, -0.009] * 20
        annualized = _stdev(returns) * math.sqrt(252)
        assert 0.15 < annualized < 0.17