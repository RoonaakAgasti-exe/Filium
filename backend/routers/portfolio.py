"""
routers/portfolio.py — /portfolio endpoints.

  GET /portfolio               current holdings + valuation
  GET /portfolio/history       daily snapshots
  GET /portfolio/transactions  trade log, with explanations
  GET /portfolio/analytics     Sharpe, drawdown, win rate, sector exposure
  GET /portfolio/vs-benchmark  return vs. SPY over the same window
"""

from fastapi import APIRouter, Depends, Query
from psycopg2.extensions import connection as PgConnection

import analytics
import market_data
from auth import get_current_user_id
from db import get_conn
from trading_models import PortfolioResponse
from wallet import ensure_wallet, get_portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

def _load_portfolio(conn: PgConnection, user_id: int) -> dict:
    ensure_wallet(conn, user_id)
    conn.commit()

    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT ticker FROM holdings WHERE user_id = %s", (user_id,))
        tickers = [row[0] for row in cur.fetchall()]
    finally:
        cur.close()

    quotes = market_data.get_prices(conn, tickers)
    prices = {ticker: q["price"] for ticker, q in quotes.items()}
    return get_portfolio(conn, user_id, prices)

@router.get("", response_model=PortfolioResponse)
def read_portfolio(user_id: int = Depends(get_current_user_id),
                   conn: PgConnection = Depends(get_conn)):
    return _load_portfolio(conn, user_id)

@router.get("/history")
def read_portfolio_history(user_id: int = Depends(get_current_user_id),
                           conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, total_value, cash_value, holdings_value "
            "FROM portfolio_snapshots WHERE user_id = %s ORDER BY date",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "date": str(r[0]),
            "total_value": float(r[1]),
            "cash_value": float(r[2]),
            "holdings_value": float(r[3]),
        }
        for r in rows
    ]

@router.get("/transactions")
def read_transactions(limit: int = Query(default=20, ge=1, le=200),
                      user_id: int = Depends(get_current_user_id),
                      conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT t.id, t.ticker, t.action, t.shares, t.price_per_share, t.realized_pl,
                   t.executed_at, t.triggered_by_prediction, te.explanation
            FROM transactions t
            LEFT JOIN trade_explanations te ON te.transaction_id = t.id
            WHERE t.user_id = %s
            ORDER BY t.executed_at DESC, t.id DESC
            LIMIT %s
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "id": r[0],
            "ticker": r[1],
            "action": r[2],
            "shares": float(r[3]),
            "price_per_share": float(r[4]),
            "amount": float(r[3]) * float(r[4]),
            "realized_pl": float(r[5]) if r[5] is not None else None,
            "executed_at": r[6].isoformat(),
            "triggered_by_prediction": r[7],
            "explanation": r[8],
        }
        for r in rows
    ]

@router.get("/analytics")
def read_analytics(user_id: int = Depends(get_current_user_id),
                   conn: PgConnection = Depends(get_conn)):
    """
    Portfolio analytics (PDF: "Portfolio analytics") — Sharpe ratio, max
    drawdown, per-trade win rate and sector exposure, computed from
    `portfolio_snapshots` and `transactions`.

    Metrics that need a history return null with a `note` explaining what
    is missing, rather than a zero that reads like a real measurement.
    """
    portfolio = _load_portfolio(conn, user_id)
    result = analytics.portfolio_analytics(conn, user_id, portfolio["holdings"])
    result["current_value"] = portfolio["total_value"]
    result["cash_balance"] = portfolio["cash_balance"]
    result["holdings_value"] = portfolio["holdings_value"]
    return result

@router.get("/vs-benchmark")
def read_vs_benchmark(user_id: int = Depends(get_current_user_id),
                      conn: PgConnection = Depends(get_conn)):
    """
    Compares portfolio return since the first snapshot against SPY's
    return over the same period, using price_history if SPY has been
    ingested as a ticker (treat it like any other watchlisted ticker).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, total_value FROM portfolio_snapshots WHERE user_id = %s ORDER BY date",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    if len(rows) < 2:
        return {
            "portfolio_return": None,
            "benchmark_return": None,
            "message": "Not enough snapshot history yet to compute a comparison. "
                       "The scheduler records one snapshot per day.",
        }

    first_date, first_value = rows[0]
    last_date, last_value = rows[-1]
    first_value, last_value = float(first_value), float(last_value)

    if not first_value:
        return {"portfolio_return": None, "benchmark_return": None,
                "message": "First snapshot has zero value; cannot compute a return."}

    portfolio_return = (last_value - first_value) / first_value

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT close FROM price_history WHERE ticker = 'SPY' AND date >= %s "
            "AND close IS NOT NULL ORDER BY date LIMIT 1",
            (first_date,),
        )
        spy_start_row = cur.fetchone()
        cur.execute(
            "SELECT close FROM price_history WHERE ticker = 'SPY' AND date <= %s "
            "AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
            (last_date,),
        )
        spy_end_row = cur.fetchone()
    finally:
        cur.close()

    if not spy_start_row or not spy_end_row or not spy_start_row[0]:
        return {
            "portfolio_return": portfolio_return,
            "benchmark_return": None,
            "start_date": str(first_date),
            "end_date": str(last_date),
            "message": "SPY price history not ingested — benchmark comparison unavailable. "
                       "Run: python ingestion/fetch_prices.py SPY",
        }

    spy_start, spy_end = float(spy_start_row[0]), float(spy_end_row[0])
    benchmark_return = (spy_end - spy_start) / spy_start

    return {
        "portfolio_return": portfolio_return,
        "benchmark_return": benchmark_return,
        "excess_return": portfolio_return - benchmark_return,
        "start_date": str(first_date),
        "end_date": str(last_date),
    }
