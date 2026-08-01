"""
market_data.py

Resolves "what is this ticker worth right now?" from whichever source is
actually available, in order of freshness:

    1. Alpaca's latest trade   (live, needs API keys)
    2. Latest close in price_history (stale by up to a day, always local)

The fallback is what lets the whole paper-trading half of the app work on
a machine with no Alpaca account at all — you get yesterday's close
instead of a live tick, clearly labelled as such, rather than a 502.
"""

import logging

import alpaca_client
from alpaca_client import AlpacaUnavailable

logger = logging.getLogger("fincopilot.market_data")


class PriceUnavailable(RuntimeError):
    """No source could produce a price for this ticker."""


def latest_close_from_db(conn, ticker: str) -> tuple[float, str] | None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT close, date FROM price_history "
            "WHERE ticker = %s AND close IS NOT NULL ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None or row[0] is None:
        return None
    return float(row[0]), str(row[1])


def get_price(conn, ticker: str) -> dict:
    """
    Returns {'price': float, 'source': str, 'as_of': str|None}.
    Raises PriceUnavailable if nothing can price the ticker.
    """
    ticker = ticker.upper()

    if alpaca_client.is_configured():
        try:
            return {"price": alpaca_client.get_latest_price(ticker), "source": "alpaca", "as_of": None}
        except AlpacaUnavailable as exc:
            logger.info("Alpaca price lookup failed for %s, falling back to stored close: %s", ticker, exc)

    stored = latest_close_from_db(conn, ticker)
    if stored is not None:
        price, as_of = stored
        return {"price": price, "source": "price_history", "as_of": as_of}

    raise PriceUnavailable(
        f"No price available for {ticker}. Configure Alpaca API keys for live quotes, "
        f"or ingest price history first: python ingestion/fetch_prices.py {ticker}"
    )


def get_prices(conn, tickers: list[str]) -> dict[str, dict]:
    """Best-effort bulk lookup — tickers that can't be priced are omitted."""
    out: dict[str, dict] = {}
    for ticker in tickers:
        try:
            out[ticker] = get_price(conn, ticker)
        except PriceUnavailable:
            logger.info("No price source for %s; leaving it unpriced", ticker)
    return out


def ensure_company(conn, ticker: str, name: str | None = None, sector: str | None = None) -> None:
    """
    Guarantees a `companies` row exists so foreign keys on watchlists,
    holdings and transactions hold. Without this a trade on a
    never-ingested ticker fails with a raw ForeignKeyViolation, which
    surfaces as an opaque 500 *and* aborts the transaction.
    """
    ticker = ticker.upper()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO companies (ticker, name, sector) VALUES (%s, %s, %s) "
            "ON CONFLICT (ticker) DO UPDATE SET "
            "  name = COALESCE(NULLIF(EXCLUDED.name, EXCLUDED.ticker), companies.name), "
            "  sector = COALESCE(EXCLUDED.sector, companies.sector)",
            (ticker, name or ticker, sector),
        )
    finally:
        cur.close()


def company_exists(conn, ticker: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM companies WHERE ticker = %s", (ticker.upper(),))
        return cur.fetchone() is not None
    finally:
        cur.close()
