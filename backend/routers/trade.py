"""routers/trade.py — /trade/buy and /trade/sell (paper trading only)."""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extensions import connection as PgConnection

import explain
import market_data
from auth import get_current_user_id
from db import get_conn
from trading_models import TradeRequest, TradeResponse
from wallet import (
    InsufficientFundsError,
    InsufficientSharesError,
    buy_shares,
    ensure_wallet,
    sell_shares,
)

logger = logging.getLogger("fincopilot.trade")

router = APIRouter(prefix="/trade", tags=["trade"])

def _execute(conn: PgConnection, user_id: int, payload: TradeRequest, action: str) -> dict:
    ticker = payload.ticker.strip().upper()

    quote = market_data.get_price(conn, ticker)
    price = quote["price"]

    try:
        market_data.ensure_company(conn, ticker)
        ensure_wallet(conn, user_id)

        if action == "buy":
            result = buy_shares(conn, user_id, ticker, payload.shares, price,
                                payload.triggered_by_prediction)
        else:
            result = sell_shares(conn, user_id, ticker, payload.shares, price,
                                 payload.triggered_by_prediction)

        explanation = None
        if payload.explain:
            try:
                explanation = explain.explain_trade(
                    conn, result["transaction_id"], action, ticker,
                    payload.shares, price, payload.triggered_by_prediction,
                )
            except Exception:
                logger.exception("Trade explanation failed for transaction %s", result["transaction_id"])

        conn.commit()
    except (InsufficientFundsError, InsufficientSharesError) as exc:
        conn.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except market_data.PriceUnavailable:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise

    return {
        "executed_price": price,
        "price_source": quote["source"],
        "explanation": explanation,
        **result,
    }

@router.post("/buy", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
def buy(payload: TradeRequest, user_id: int = Depends(get_current_user_id),
        conn: PgConnection = Depends(get_conn)):
    return _execute(conn, user_id, payload, "buy")

@router.post("/sell", response_model=TradeResponse, status_code=status.HTTP_201_CREATED)
def sell(payload: TradeRequest, user_id: int = Depends(get_current_user_id),
         conn: PgConnection = Depends(get_conn)):
    return _execute(conn, user_id, payload, "sell")

@router.get("/explanation/{transaction_id}")
def get_explanation(transaction_id: int, user_id: int = Depends(get_current_user_id),
                    conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT te.explanation, te.sentiment_score, te.created_at,
                   t.ticker, t.action, t.shares, t.price_per_share
            FROM trade_explanations te
            JOIN transactions t ON t.id = te.transaction_id
            WHERE te.transaction_id = %s AND t.user_id = %s
            """,
            (transaction_id, user_id),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No explanation recorded for that transaction")

    return {
        "transaction_id": transaction_id,
        "explanation": row[0],
        "sentiment_score": float(row[1]) if row[1] is not None else None,
        "created_at": row[2].isoformat(),
        "ticker": row[3],
        "action": row[4],
        "shares": float(row[5]),
        "price_per_share": float(row[6]),
    }
