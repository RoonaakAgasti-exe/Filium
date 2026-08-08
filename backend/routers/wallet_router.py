"""routers/wallet_router.py — virtual cash management (no broker required)."""

from typing import Literal

from fastapi import APIRouter, Depends
from psycopg2.extensions import connection as PgConnection
from pydantic import BaseModel, Field

import config
from auth import get_current_user_id
from db import get_conn
from wallet import deposit_cash, ensure_wallet, get_cash_balance

router = APIRouter(prefix="/wallet", tags=["wallet"])

class WalletDepositRequest(BaseModel):
    amount: float | None = Field(default=None, gt=0, le=10_000_000)
    mode: Literal["add", "set"] = "add"

@router.post("/deposit")
def deposit(payload: WalletDepositRequest, user_id: int = Depends(get_current_user_id),
            conn: PgConnection = Depends(get_conn)):
    """
    Top up or reset virtual cash on the in-app ledger. Paper trading never
    touches a brokerage API — this only adjusts the Postgres wallet row.
    """
    amount = payload.amount if payload.amount is not None else config.STARTING_CASH
    ensure_wallet(conn, user_id)
    new_balance = deposit_cash(conn, user_id, amount, mode=payload.mode)
    return {
        "cash_balance": new_balance,
        "mode": payload.mode,
        "amount": amount,
    }

@router.get("")
def read_wallet(user_id: int = Depends(get_current_user_id),
                conn: PgConnection = Depends(get_conn)):
    ensure_wallet(conn, user_id)
    conn.commit()
    return {"cash_balance": get_cash_balance(conn, user_id)}
