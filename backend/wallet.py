"""
wallet.py

Core buy/sell/portfolio-valuation logic, kept separate from the FastAPI
router so it can be tested directly against a database without going
through HTTP or needing Alpaca's live API.

Every mutation locks the wallet row with SELECT ... FOR UPDATE before
reading the balance it is about to modify. Read-then-write without the
lock lets two concurrent buys both observe the same starting cash and
both succeed, overdrawing the account — which is easy to hit here
because the frontend fires trades from two different pages.
"""

import logging
from datetime import date

from psycopg2.extensions import connection as PgConnection

import config

logger = logging.getLogger("fincopilot.wallet")


class InsufficientFundsError(Exception):
    pass


class InsufficientSharesError(Exception):
    pass


def ensure_wallet(conn: PgConnection, user_id: int) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO wallets (user_id, cash_balance) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO NOTHING",
            (user_id, config.STARTING_CASH),
        )
    finally:
        cur.close()


def get_cash_balance(conn: PgConnection, user_id: int) -> float:
    cur = conn.cursor()
    try:
        cur.execute("SELECT cash_balance FROM wallets WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        raise ValueError(f"No wallet found for user {user_id}")
    return float(row[0])


def _lock_cash_balance(cur, user_id: int) -> float:
    cur.execute("SELECT cash_balance FROM wallets WHERE user_id = %s FOR UPDATE", (user_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"No wallet found for user {user_id}")
    return float(row[0])


def get_holding(conn: PgConnection, user_id: int, ticker: str) -> dict | None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT shares, avg_cost_basis FROM holdings WHERE user_id = %s AND ticker = %s",
            (user_id, ticker),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return None
    return {"shares": float(row[0]), "avg_cost_basis": float(row[1])}


def _lock_holding(cur, user_id: int, ticker: str) -> dict | None:
    cur.execute(
        "SELECT shares, avg_cost_basis FROM holdings "
        "WHERE user_id = %s AND ticker = %s FOR UPDATE",
        (user_id, ticker),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"shares": float(row[0]), "avg_cost_basis": float(row[1])}


def buy_shares(conn: PgConnection, user_id: int, ticker: str, shares: float,
               price: float, triggered_by_prediction: bool = False) -> dict:
    """
    Deducts cost from cash, updates (or creates) the holding with a
    weighted-average cost basis, and logs the transaction. Raises
    InsufficientFundsError without committing anything if the user
    can't afford it.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    cost = shares * price
    cur = conn.cursor()

    try:
        cash = _lock_cash_balance(cur, user_id)

        # Round to cents before comparing: a cost of 100000.000000001 from
        # float multiplication would otherwise reject a trade the user can
        # exactly afford.
        if round(cost, 2) > round(cash, 2):
            raise InsufficientFundsError(
                f"Cost ${cost:,.2f} exceeds available cash ${cash:,.2f}"
            )

        new_cash = cash - cost
        cur.execute("UPDATE wallets SET cash_balance = %s WHERE user_id = %s", (new_cash, user_id))

        existing = _lock_holding(cur, user_id, ticker)
        if existing is None:
            cur.execute(
                "INSERT INTO holdings (user_id, ticker, shares, avg_cost_basis) "
                "VALUES (%s, %s, %s, %s)",
                (user_id, ticker, shares, price),
            )
            new_shares, new_avg_cost = shares, price
        else:
            old_shares, old_avg_cost = existing["shares"], existing["avg_cost_basis"]
            new_shares = old_shares + shares
            # weighted average: total cost so far / total shares so far
            new_avg_cost = ((old_shares * old_avg_cost) + (shares * price)) / new_shares
            cur.execute(
                "UPDATE holdings SET shares = %s, avg_cost_basis = %s "
                "WHERE user_id = %s AND ticker = %s",
                (new_shares, new_avg_cost, user_id, ticker),
            )

        cur.execute(
            "INSERT INTO transactions "
            "(user_id, ticker, action, shares, price_per_share, triggered_by_prediction) "
            "VALUES (%s, %s, 'buy', %s, %s, %s) RETURNING id",
            (user_id, ticker, shares, price, triggered_by_prediction),
        )
        transaction_id = cur.fetchone()[0]

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {
        "transaction_id": transaction_id,
        "new_cash_balance": new_cash,
        "new_shares": new_shares,
        "new_avg_cost_basis": new_avg_cost,
        "realized_pl": None,
    }


def sell_shares(conn: PgConnection, user_id: int, ticker: str, shares: float,
                price: float, triggered_by_prediction: bool = False) -> dict:
    """
    Adds proceeds to cash, reduces the holding (deleting it if it hits
    zero), and logs the transaction along with the realized P&L for that
    sale. Raises InsufficientSharesError without committing anything if
    the user doesn't own enough shares.

    Cost basis is NOT recalculated on a sell — averaging only applies
    when adding to a position; selling just draws down share count.
    """
    if shares <= 0:
        raise ValueError("shares must be positive")
    if price <= 0:
        raise ValueError("price must be positive")

    cur = conn.cursor()

    try:
        # Lock in the same order as buy_shares (wallet, then holding) so
        # concurrent buy/sell on one account can't deadlock against each other.
        cash = _lock_cash_balance(cur, user_id)
        existing = _lock_holding(cur, user_id, ticker)

        owned = existing["shares"] if existing else 0.0
        if existing is None or round(owned, 6) < round(shares, 6):
            raise InsufficientSharesError(
                f"Cannot sell {shares:g} shares of {ticker}, only {owned:g} owned"
            )

        proceeds = shares * price
        realized_pl = shares * (price - existing["avg_cost_basis"])
        new_cash = cash + proceeds

        cur.execute("UPDATE wallets SET cash_balance = %s WHERE user_id = %s", (new_cash, user_id))

        remaining_shares = owned - shares
        if round(remaining_shares, 6) <= 0:
            remaining_shares = 0.0
            cur.execute("DELETE FROM holdings WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        else:
            cur.execute(
                "UPDATE holdings SET shares = %s WHERE user_id = %s AND ticker = %s",
                (remaining_shares, user_id, ticker),
            )

        cur.execute(
            "INSERT INTO transactions "
            "(user_id, ticker, action, shares, price_per_share, realized_pl, triggered_by_prediction) "
            "VALUES (%s, %s, 'sell', %s, %s, %s, %s) RETURNING id",
            (user_id, ticker, shares, price, realized_pl, triggered_by_prediction),
        )
        transaction_id = cur.fetchone()[0]

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {
        "transaction_id": transaction_id,
        "new_cash_balance": new_cash,
        "remaining_shares": remaining_shares,
        "realized_pl": realized_pl,
    }


def get_portfolio(conn: PgConnection, user_id: int, current_prices: dict[str, float]) -> dict:
    """
    current_prices: {ticker: latest_price} — caller fetches these
    (e.g. via market_data.get_prices) since this module has no network
    access of its own, keeping it testable without live data.
    """
    cash = get_cash_balance(conn, user_id)

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT h.ticker, h.shares, h.avg_cost_basis, c.sector "
            "FROM holdings h LEFT JOIN companies c ON c.ticker = h.ticker "
            "WHERE h.user_id = %s ORDER BY h.ticker",
            (user_id,),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    holdings = []
    holdings_value = 0.0
    for ticker, shares, avg_cost_basis, sector in rows:
        shares, avg_cost_basis = float(shares), float(avg_cost_basis)
        price = current_prices.get(ticker)
        market_value = shares * price if price is not None else None
        cost_value = shares * avg_cost_basis
        unrealized_pl = (market_value - cost_value) if market_value is not None else None
        unrealized_pl_pct = (unrealized_pl / cost_value) if (unrealized_pl is not None and cost_value) else None

        holdings.append({
            "ticker": ticker,
            "shares": shares,
            "avg_cost_basis": avg_cost_basis,
            "sector": sector,
            "current_price": price,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
            "unrealized_pl_pct": unrealized_pl_pct,
        })
        if market_value is not None:
            holdings_value += market_value

    return {
        "cash_balance": cash,
        "holdings": holdings,
        "holdings_value": holdings_value,
        "total_value": cash + holdings_value,
    }


def save_daily_snapshot(conn: PgConnection, user_id: int, as_of: date, portfolio: dict) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO portfolio_snapshots (user_id, date, total_value, cash_value, holdings_value)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, date) DO UPDATE
                SET total_value = EXCLUDED.total_value,
                    cash_value = EXCLUDED.cash_value,
                    holdings_value = EXCLUDED.holdings_value
            """,
            (user_id, as_of, portfolio["total_value"], portfolio["cash_balance"],
             portfolio["holdings_value"]),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
