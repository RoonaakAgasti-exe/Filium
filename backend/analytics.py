# analytics.py — Calculates portfolio performance and risk metrics.
pass

import math

TRADING_DAYS_PER_YEAR = 252

def daily_returns(values: list[float]) -> list[float]:
    pass
    out = []
    for prev, curr in zip(values, values[1:]):
        if prev:
            out.append((curr - prev) / prev)
    return out

def sharpe_ratio(returns: list[float], risk_free_rate: float = 0.0,
                 periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float | None:
    pass

    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None

    excess = mean - (risk_free_rate / periods_per_year)
    return (excess / std) * math.sqrt(periods_per_year)

def max_drawdown(values: list[float]) -> dict | None:
    pass

    if len(values) < 2:
        return None

    peak = values[0]
    peak_index = 0
    worst = 0.0
    worst_peak_index = 0
    worst_trough_index = 0

    for i, value in enumerate(values):
        if value > peak:
            peak = value
            peak_index = i
        if peak:
            drawdown = (value - peak) / peak
            if drawdown < worst:
                worst = drawdown
                worst_peak_index = peak_index
                worst_trough_index = i

    return {
        "max_drawdown": worst,
        "peak_index": worst_peak_index,
        "trough_index": worst_trough_index,
    }

def trade_stats(sells: list[dict]) -> dict:
    pass

    realized = [float(s["realized_pl"]) for s in sells if s.get("realized_pl") is not None]

    if not realized:
        return {
            "closed_trades": 0, "wins": 0, "losses": 0, "win_rate": None,
            "total_realized_pl": 0.0, "avg_win": None, "avg_loss": None,
            "profit_factor": None, "best_trade": None, "worst_trade": None,
        }

    wins = [r for r in realized if r > 0]
    losses = [r for r in realized if r < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return {
        "closed_trades": len(realized),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(realized),
        "total_realized_pl": sum(realized),
        "avg_win": (gross_profit / len(wins)) if wins else None,
        "avg_loss": (-gross_loss / len(losses)) if losses else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else None,
        "best_trade": max(realized),
        "worst_trade": min(realized),
    }

def sector_exposure(holdings: list[dict]) -> list[dict]:
    pass

    by_sector: dict[str, float] = {}
    total = 0.0

    for h in holdings:
        market_value = h.get("market_value")
        if market_value is None:
            continue
        sector = h.get("sector") or "Unclassified"
        by_sector[sector] = by_sector.get(sector, 0.0) + market_value
        total += market_value

    if not total:
        return []

    return sorted(
        [{"sector": s, "market_value": v, "weight": v / total} for s, v in by_sector.items()],
        key=lambda r: r["market_value"],
        reverse=True,
    )

def calibration_curve(predictions: list[dict], num_bins: int = 5) -> dict:
    pass

    usable = [
        p for p in predictions
        if p.get("prob_up") is not None and p.get("actual_direction") in ("up", "down")
    ]

    bins = []
    for i in range(num_bins):
        low = i / num_bins
        high = (i + 1) / num_bins
        members = [
            p for p in usable
            if low <= float(p["prob_up"]) < high
            or (i == num_bins - 1 and float(p["prob_up"]) == 1.0)
        ]

        if members:
            mean_predicted = sum(float(p["prob_up"]) for p in members) / len(members)
            observed = sum(1 for p in members if p["actual_direction"] == "up") / len(members)
        else:
            mean_predicted = None
            observed = None

        bins.append({
            "bin_lower": low,
            "bin_upper": high,
            "count": len(members),
            "mean_predicted_probability": mean_predicted,
            "observed_frequency": observed,
        })

    total = len(usable)
    ece = None
    if total:
        ece = sum(
            (b["count"] / total) * abs(b["mean_predicted_probability"] - b["observed_frequency"])
            for b in bins if b["count"]
        )

    brier = None
    if total:
        brier = sum(
            (float(p["prob_up"]) - (1.0 if p["actual_direction"] == "up" else 0.0)) ** 2
            for p in usable
        ) / total

    return {
        "bins": bins,
        "num_resolved": total,
        "expected_calibration_error": ece,
        "brier_score": brier,
    }

def portfolio_analytics(conn, user_id: int, holdings: list[dict]) -> dict:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, total_value FROM portfolio_snapshots WHERE user_id = %s ORDER BY date",
            (user_id,),
        )
        snapshots = [(row[0], float(row[1])) for row in cur.fetchall()]

        cur.execute(
            "SELECT realized_pl FROM transactions "
            "WHERE user_id = %s AND action = 'sell' AND realized_pl IS NOT NULL",
            (user_id,),
        )
        sells = [{"realized_pl": row[0]} for row in cur.fetchall()]
    finally:
        cur.close()

    dates = [d for d, _ in snapshots]
    values = [v for _, v in snapshots]

    returns = daily_returns(values)
    drawdown = max_drawdown(values)

    if drawdown and dates:
        drawdown = {
            "max_drawdown": drawdown["max_drawdown"],
            "peak_date": str(dates[drawdown["peak_index"]]),
            "trough_date": str(dates[drawdown["trough_index"]]),
        }

    total_return = None
    if len(values) >= 2 and values[0]:
        total_return = (values[-1] - values[0]) / values[0]

    return {
        "snapshot_count": len(snapshots),
        "period_start": str(dates[0]) if dates else None,
        "period_end": str(dates[-1]) if dates else None,
        "total_return": total_return,
        "sharpe_ratio": sharpe_ratio(returns),
        "volatility_annualized": (
            _stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR) if len(returns) >= 2 else None
        ),
        "max_drawdown": drawdown,
        "trades": trade_stats(sells),
        "sector_exposure": sector_exposure(holdings),
        "note": (
            None if len(snapshots) >= 2 else
            "Sharpe, volatility and drawdown need at least two daily snapshots. "
            "The scheduler records one per night; run `python ml/scheduler.py --now` "
            "to take one immediately."
        ),
    }

def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))

def prediction_calibration(conn, ticker: str | None = None, model_version_id: int | None = None,
                           num_bins: int = 5) -> dict:
    sql = [
        "SELECT p.ticker, p.prob_up, p.confidence, p.actual_direction, p.predicted_direction,",
        "       p.prediction_date, m.name AS model_name",
        "FROM predictions p",
        "JOIN model_versions m ON m.id = p.model_version_id",
        "WHERE p.actual_direction IS NOT NULL",
    ]
    params: list = []

    if ticker:
        sql.append("AND p.ticker = %s")
        params.append(ticker.upper())
    if model_version_id is not None:
        sql.append("AND p.model_version_id = %s")
        params.append(model_version_id)

    sql.append("ORDER BY p.prediction_date")

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()

    result = calibration_curve(rows, num_bins=num_bins)
    result["ticker"] = ticker.upper() if ticker else None

    missing_prob = sum(1 for r in rows if r["prob_up"] is None)
    if missing_prob:
        result["note"] = (
            f"{missing_prob} resolved prediction(s) predate probability logging and are "
            "excluded from the curve."
        )
    return result

def accuracy_summary(conn, ticker: str | None = None, model_version_id: int | None = None) -> dict:
    sql = [
        "SELECT count(*) AS total,",
        "       count(actual_direction) AS resolved,",
        "       count(*) FILTER (WHERE predicted_direction = actual_direction) AS correct",
        "FROM predictions WHERE TRUE",
    ]
    params: list = []
    if ticker:
        sql.append("AND ticker = %s")
        params.append(ticker.upper())
    if model_version_id is not None:
        sql.append("AND model_version_id = %s")
        params.append(model_version_id)

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        total, resolved, correct = cur.fetchone()
    finally:
        cur.close()

    return {
        "total_predictions": total,
        "resolved_predictions": resolved,
        "correct_predictions": correct,
        "accuracy": (correct / resolved) if resolved else None,
    }