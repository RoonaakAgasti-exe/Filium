"""
routers/public.py — unauthenticated, read-only ticker pages
(PDF: "Social / shareable predictions").

A shareable link showing the current signal and its accuracy track record.
Deliberately exposes nothing user-specific: no portfolio, no holdings, no
watchlist, no query history — only ticker-level model output that is the
same for every visitor.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PgConnection

import analytics
from db import get_conn

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/tickers")
def list_public_tickers(conn: PgConnection = Depends(get_conn)):
    """Every ticker with at least one prediction — the index of shareable pages."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.ticker, c.name, c.sector, count(*) AS prediction_count,
                   max(p.prediction_date) AS latest
            FROM predictions p
            JOIN companies c ON c.ticker = p.ticker
            GROUP BY p.ticker, c.name, c.sector
            ORDER BY p.ticker
            """
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "ticker": r[0], "name": r[1], "sector": r[2],
            "prediction_count": r[3], "latest_prediction": str(r[4]),
        }
        for r in rows
    ]


@router.get("/{ticker}")
def public_ticker_page(ticker: str, history_days: int = Query(default=60, ge=1, le=365),
                       conn: PgConnection = Depends(get_conn)):
    ticker = ticker.upper()

    cur = conn.cursor()
    try:
        cur.execute("SELECT ticker, name, sector FROM companies WHERE ticker = %s", (ticker,))
        company = cur.fetchone()

        cur.execute(
            """
            SELECT p.prediction_date, p.target_date, p.predicted_direction, p.confidence,
                   p.prob_up, p.actual_direction, m.name
            FROM predictions p
            JOIN model_versions m ON m.id = p.model_version_id
            WHERE p.ticker = %s
            ORDER BY m.is_active DESC, p.prediction_date DESC, p.id DESC
            LIMIT 1
            """,
            (ticker,),
        )
        latest = cur.fetchone()

        cur.execute(
            """
            SELECT prediction_date, predicted_direction, confidence, actual_direction
            FROM predictions
            WHERE ticker = %s
            ORDER BY prediction_date DESC
            LIMIT %s
            """,
            (ticker, history_days),
        )
        history = list(reversed(cur.fetchall()))

        cur.execute(
            "SELECT date, close FROM price_history WHERE ticker = %s AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT %s",
            (ticker, history_days),
        )
        closes = list(reversed(cur.fetchall()))
    finally:
        cur.close()

    if company is None and latest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Nothing published for {ticker}")

    summary = analytics.accuracy_summary(conn, ticker=ticker)
    calibration = analytics.prediction_calibration(conn, ticker=ticker)

    return {
        "ticker": ticker,
        "name": company[1] if company else ticker,
        "sector": company[2] if company else None,
        "latest_prediction": (
            {
                "prediction_date": str(latest[0]),
                "target_date": str(latest[1]),
                "predicted_direction": latest[2],
                "confidence": float(latest[3]),
                "prob_up": float(latest[4]) if latest[4] is not None else None,
                "actual_direction": latest[5],
                "model_name": latest[6],
            }
            if latest else None
        ),
        "track_record": summary,
        "calibration": {
            "brier_score": calibration["brier_score"],
            "expected_calibration_error": calibration["expected_calibration_error"],
            "num_resolved": calibration["num_resolved"],
        },
        "history": [
            {
                "prediction_date": str(r[0]),
                "predicted_direction": r[1],
                "confidence": float(r[2]),
                "actual_direction": r[3],
                "correct": (r[1] == r[3]) if r[3] else None,
            }
            for r in history
        ],
        "closes": [{"date": str(r[0]), "close": float(r[1])} for r in closes],
        "disclaimer": (
            "Model output for research purposes only. Paper trading — not investment advice."
        ),
    }
