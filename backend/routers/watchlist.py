"""routers/watchlist.py — /watchlist endpoints."""

from fastapi import APIRouter, Depends, status
from psycopg2.extensions import connection as PgConnection

import market_data
from auth import get_current_user_id
from db import get_conn
from models import WatchlistAdd, WatchlistItem

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[WatchlistItem])
def get_watchlist(user_id: int = Depends(get_current_user_id),
                  conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ticker, added_at FROM watchlists WHERE user_id = %s ORDER BY added_at",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()
    return [WatchlistItem(ticker=r[0], added_at=r[1]) for r in rows]


@router.get("/detailed")
def get_watchlist_detailed(user_id: int = Depends(get_current_user_id),
                           conn: PgConnection = Depends(get_conn)):
    """Watchlist rows enriched with the latest signal and close, for the dashboard."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT w.ticker, w.added_at, c.name, c.sector,
                   p.predicted_direction, p.confidence, p.prediction_date,
                   (SELECT close FROM price_history ph
                     WHERE ph.ticker = w.ticker ORDER BY ph.date DESC LIMIT 1) AS last_close
            FROM watchlists w
            JOIN companies c ON c.ticker = w.ticker
            LEFT JOIN LATERAL (
                SELECT predicted_direction, confidence, prediction_date
                FROM predictions
                WHERE ticker = w.ticker
                ORDER BY prediction_date DESC, id DESC
                LIMIT 1
            ) p ON TRUE
            WHERE w.user_id = %s
            ORDER BY w.added_at
            """,
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "ticker": r[0],
            "added_at": r[1].isoformat(),
            "name": r[2],
            "sector": r[3],
            "predicted_direction": r[4],
            "confidence": float(r[5]) if r[5] is not None else None,
            "prediction_date": str(r[6]) if r[6] else None,
            "last_close": float(r[7]) if r[7] is not None else None,
        }
        for r in rows
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def add_to_watchlist(payload: WatchlistAdd, user_id: int = Depends(get_current_user_id),
                     conn: PgConnection = Depends(get_conn)):
    """
    Adds a ticker, creating the `companies` row if it doesn't exist yet.

    The original version rejected anything not already ingested, which
    made the watchlist unusable on a fresh install: the nightly prediction
    job only runs for watchlisted tickers, so nothing could ever be
    watchlisted and nothing could ever be predicted.
    """
    ticker = payload.ticker.strip().upper()

    cur = conn.cursor()
    try:
        market_data.ensure_company(conn, ticker)
        cur.execute(
            "INSERT INTO watchlists (user_id, ticker) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user_id, ticker),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {"message": f"{ticker} added to watchlist", "ticker": ticker}


@router.delete("/{ticker}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(ticker: str, user_id: int = Depends(get_current_user_id),
                          conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM watchlists WHERE user_id = %s AND ticker = %s",
            (user_id, ticker.upper()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
