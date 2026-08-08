# sandbox.py — API router for the backtesting sandbox.
pass

from fastapi import APIRouter, Depends
from psycopg2.extensions import connection as PgConnection

import backtest_sim
from auth import get_current_user_id
from db import get_conn
from models import SandboxRequest

router = APIRouter(prefix="/sandbox", tags=["sandbox"])

@router.post("/backtest")
def run_backtest(payload: SandboxRequest, user_id: int = Depends(get_current_user_id),
                 conn: PgConnection = Depends(get_conn)):
    return backtest_sim.run(
        conn,
        payload.ticker,
        start=payload.start_date,
        end=payload.end_date,
        starting_cash=payload.starting_cash,
        confidence_threshold=payload.confidence_threshold,
        model_version_id=payload.model_version_id,
    )

@router.get("/available")
def available_tickers(conn: PgConnection = Depends(get_conn)):
    pass

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ph.ticker,
                   min(ph.date) AS first_date,
                   max(ph.date) AS last_date,
                   count(*) AS trading_days,
                   (SELECT count(*) FROM predictions p WHERE p.ticker = ph.ticker) AS predictions
            FROM price_history ph
            WHERE ph.close IS NOT NULL
            GROUP BY ph.ticker
            HAVING count(*) >= 2
            ORDER BY ph.ticker
            """
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "ticker": r[0],
            "first_date": str(r[1]),
            "last_date": str(r[2]),
            "trading_days": r[3],
            "prediction_count": r[4],
        }
        for r in rows
    ]