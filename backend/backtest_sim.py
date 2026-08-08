"""
backtest_sim.py

Replays the paper strategy over a historical window using predictions
that were already logged, so a user can ask "what would this have done
between March and June?" (PDF: "Backtesting sandbox UI").

The strategy is deliberately simple — hold the stock on days the model
said UP with enough confidence, hold cash otherwise — because the point
is to show what the signal was worth, not to present a tuned strategy.

Two honesty rules are baked in:

  * A prediction made on day D is acted on using day D's close, and earns
    day D+1's return. Using day D+1's own prediction to earn day D+1's
    return is lookahead, and it is the single easiest way to produce a
    backtest that looks excellent and means nothing.
  * Buy-and-hold over the identical window is always returned alongside.
    A strategy return with no benchmark is unfalsifiable.
"""

from datetime import date

import analytics

def _load_series(conn, ticker: str, start: date | None, end: date | None,
                 model_version_id: int | None) -> list[dict]:
    """Joins each trading day's close to that day's prediction, chronologically."""
    sql = [
        "SELECT ph.date, ph.close, p.predicted_direction, p.confidence, p.prob_up",
        "FROM price_history ph",
        "LEFT JOIN LATERAL (",
        "    SELECT predicted_direction, confidence, prob_up",
        "    FROM predictions",
        "    WHERE ticker = ph.ticker AND prediction_date = ph.date",
    ]
    params: list = []

    if model_version_id is not None:
        sql.append("      AND model_version_id = %s")
        params.append(model_version_id)

    sql += [
        "    ORDER BY id DESC LIMIT 1",
        ") p ON TRUE",
        "WHERE ph.ticker = %s AND ph.close IS NOT NULL",
    ]
    params.append(ticker.upper())

    if start:
        sql.append("AND ph.date >= %s")
        params.append(start)
    if end:
        sql.append("AND ph.date <= %s")
        params.append(end)

    sql.append("ORDER BY ph.date")

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "date": r[0],
            "close": float(r[1]),
            "predicted_direction": r[2],
            "confidence": float(r[3]) if r[3] is not None else None,
            "prob_up": float(r[4]) if r[4] is not None else None,
        }
        for r in rows
    ]

def simulate(series: list[dict], starting_cash: float = 10000.0,
             confidence_threshold: float = 0.5, ticker: str = "") -> dict:
    """
    Pure simulation over an already-loaded series. Separated from the
    query so it can be unit tested against handmade data.
    """
    if len(series) < 2:
        return {
            "days": len(series),
            "error": "Need at least two days of price history in this window to simulate.",
        }

    equity = starting_cash
    curve = []
    trades = []
    in_position = False
    days_in_market = 0

    for today, tomorrow in zip(series, series[1:]):
        signal_is_up = (
            today["predicted_direction"] == "up"
            and today["confidence"] is not None
            and today["confidence"] >= confidence_threshold
        )

        day_return = (tomorrow["close"] - today["close"]) / today["close"] if today["close"] else 0.0

        if signal_is_up:
            equity *= (1 + day_return)
            days_in_market += 1
            trades.append({
                "date": str(today["date"]),
                "action": "buy",
                "ticker": ticker or "—",
                "shares": 1,
                "price": today["close"],
                "entry_close": today["close"],
                "exit_close": tomorrow["close"],
                "return": day_return,
                "confidence": today["confidence"],
            })

        if signal_is_up != in_position:
            in_position = signal_is_up

        curve.append({
            "date": str(tomorrow["date"]),
            "strategy_value": equity,
            "buy_hold_value": starting_cash * (tomorrow["close"] / series[0]["close"]),
            "in_market": signal_is_up,
        })

    strategy_returns = [t["return"] for t in trades]
    all_returns = [
        (b["close"] - a["close"]) / a["close"]
        for a, b in zip(series, series[1:]) if a["close"]
    ]

    strategy_values = [starting_cash] + [c["strategy_value"] for c in curve]
    buy_hold_values = [starting_cash] + [c["buy_hold_value"] for c in curve]

    strategy_daily = analytics.daily_returns(strategy_values)

    wins = sum(1 for r in strategy_returns if r > 0)

    strategy_drawdown = analytics.max_drawdown(strategy_values)
    buy_hold_drawdown = analytics.max_drawdown(buy_hold_values)

    predicted_days = sum(1 for d in series[:-1] if d["predicted_direction"] is not None)

    return {
        "days": len(series),
        "days_with_prediction": predicted_days,
        "days_in_market": days_in_market,
        "starting_cash": starting_cash,
        "final_value": equity,
        "strategy_return": (equity - starting_cash) / starting_cash,
        "buy_hold_final_value": buy_hold_values[-1],
        "buy_hold_return": (buy_hold_values[-1] - starting_cash) / starting_cash,
        "strategy_sharpe": analytics.sharpe_ratio(strategy_daily),
        "buy_hold_sharpe": analytics.sharpe_ratio(all_returns),
        "strategy_max_drawdown": strategy_drawdown["max_drawdown"] if strategy_drawdown else None,
        "buy_hold_max_drawdown": buy_hold_drawdown["max_drawdown"] if buy_hold_drawdown else None,
        "trade_count": len(trades),
        "trades": trades,
        "winning_days": wins,
        "win_rate": (wins / len(strategy_returns)) if strategy_returns else None,
        "equity_curve": curve,
    }

def run(conn, ticker: str, start: date | None = None, end: date | None = None,
        starting_cash: float = 10000.0, confidence_threshold: float = 0.5,
        model_version_id: int | None = None) -> dict:
    ticker = ticker.upper()
    series = _load_series(conn, ticker, start, end, model_version_id)

    if not series:
        return {
            "ticker": ticker,
            "days": 0,
            "error": f"No price history for {ticker} in that window. "
                     f"Run: python ingestion/fetch_prices.py {ticker}",
        }

    result = simulate(series, starting_cash, confidence_threshold, ticker=ticker)
    result.update({
        "ticker": ticker,
        "start_date": str(series[0]["date"]),
        "end_date": str(series[-1]["date"]),
        "confidence_threshold": confidence_threshold,
        "model_version_id": model_version_id,
    })

    if result.get("days_with_prediction") == 0:
        result["note"] = (
            "No predictions exist in this window, so the strategy never entered the "
            "market and its return is 0%. Buy-and-hold is still shown for reference."
        )

    return result
