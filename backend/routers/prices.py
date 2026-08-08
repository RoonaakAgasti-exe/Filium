"""
routers/prices.py — /prices endpoints.

Serves real historical OHLCV data out of `price_history` (populated by
`ingestion/fetch_prices.py`) so the frontend can chart actual price action
instead of a synthetic line. `/latest` returns the freshest quote any
configured source can supply — see market_data for the fallback chain —
so the app still prices a portfolio with a completely empty .env.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PgConnection

import market_data
from db import get_conn

router = APIRouter(prefix="/prices", tags=["prices"])

@router.get("/{ticker}")
def get_price_history(ticker: str, days: int = Query(default=180, ge=1, le=1825),
                      conn: PgConnection = Depends(get_conn)):
    ticker = ticker.upper()

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM price_history
            WHERE ticker = %s
            ORDER BY date DESC
            LIMIT %s
            """,
            (ticker, days),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No price history ingested for {ticker} yet. "
                   f"Run: python ingestion/fetch_prices.py {ticker}",
        )

    rows = list(reversed(rows))
    return {
        "ticker": ticker,
        "candles": [
            {
                "date": str(r[0]),
                "open": float(r[1]) if r[1] is not None else None,
                "high": float(r[2]) if r[2] is not None else None,
                "low": float(r[3]) if r[3] is not None else None,
                "close": float(r[4]) if r[4] is not None else None,
                "volume": int(r[5]) if r[5] is not None else None,
            }
            for r in rows
        ],
    }

@router.get("/{ticker}/latest")
def get_latest_quote(ticker: str, conn: PgConnection = Depends(get_conn)):
    """
    Latest price with its provenance. `source` is 'alpaca' for a live
    trade, 'fmp'/'yahoo' for a delayed quote, or 'price_history' for the
    last stored close — the UI labels them differently rather than
    passing a day-old close off as live.
    """
    result = market_data.get_price(conn, ticker)
    return {"ticker": ticker.upper(), **result}
