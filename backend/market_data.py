"""
market_data.py

Resolves "what is this ticker worth right now?" from whichever source is
actually available, in order of freshness:

    1. Alpaca's latest trade         (live, needs a brokerage account)
    2. FMP's quote endpoint          (delayed, needs a free API key)
    3. Yahoo's chart endpoint        (delayed, needs no key at all)
    4. Latest close in price_history (stale by up to a day, always local)

Only the first one requires a brokerage signup, and it is deliberately
last-resort-optional rather than load-bearing. Alpaca gates paper trading
behind identity verification that can take days, and the paper-trading
half of this app doesn't actually need a broker: the ledger in
`wallets`/`holdings` is the source of truth, and a broker would only ever
be supplying the number to multiply shares by. Tiers 2-4 supply that
number perfectly well, so a clone with a completely empty .env can still
buy, sell, and value a portfolio — just against a delayed price that the
API labels honestly via the `source` field.

Every tier is best-effort: a failure is logged and demoted to the next
one. Only the bottom falling out raises PriceUnavailable.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import alpaca_client
import config
from alpaca_client import AlpacaUnavailable

logger = logging.getLogger("fincopilot.market_data")

WEB_TIMEOUT = 8

_BROWSERISH_UA = "Mozilla/5.0 (compatible; FinCopilot/1.0)"

_quote_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()

class PriceUnavailable(RuntimeError):
    """No source could produce a price for this ticker."""

def _cache_get(ticker: str) -> dict | None:
    with _cache_lock:
        entry = _quote_cache.get(ticker)
        if entry is None:
            return None
        expires_at, quote = entry
        if time.monotonic() >= expires_at:
            _quote_cache.pop(ticker, None)
            return None
        return dict(quote)

def _cache_put(ticker: str, quote: dict) -> None:
    ttl = config.QUOTE_CACHE_TTL_SECONDS
    if ttl <= 0:
        return
    with _cache_lock:
        _quote_cache[ticker] = (time.monotonic() + ttl, dict(quote))

def clear_quote_cache() -> None:
    """Drops every cached quote. Used by tests, and after a config change."""
    with _cache_lock:
        _quote_cache.clear()

def _price_from_fmp(ticker: str) -> dict | None:
    """Delayed quote from FMP. Needs a free key; returns None without one."""
    if not config.FMP_ENABLED:
        return None

    resp = requests.get(
        "https://financialmodelingprep.com/stable/quote",
        params={"symbol": ticker, "apikey": config.FMP_API_KEY},
        timeout=WEB_TIMEOUT,
    )
    payload = resp.json()

    if isinstance(payload, dict):
        message = payload.get("Error Message") or payload.get("message")
        raise RuntimeError(message or f"unexpected FMP response (HTTP {resp.status_code})")

    if not payload:
        return None

    price = payload[0].get("price")
    if price is None:
        return None
    return {"price": float(price), "source": "fmp", "as_of": None}

def _price_from_yahoo(ticker: str) -> dict | None:
    """
    Delayed quote from Yahoo's public chart endpoint — no key, no account.
    This is the tier that makes an empty .env still work.
    """
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"interval": "1d", "range": "1d"},
        headers={"User-Agent": _BROWSERISH_UA},
        timeout=WEB_TIMEOUT,
    )
    resp.raise_for_status()

    chart = (resp.json() or {}).get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(chart["error"].get("description") or str(chart["error"]))

    results = chart.get("result") or []
    if not results:
        return None

    meta = results[0].get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    return {"price": float(price), "source": "yahoo", "as_of": None}

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

def _live_quote(ticker: str) -> dict | None:
    """
    Best live-ish price from whatever is configured, or None if every
    network tier is unavailable. Never raises: a dead upstream must not
    be able to block a trade that the stored close could price fine.
    """
    if not config.LIVE_QUOTES_ENABLED:
        return None

    cached = _cache_get(ticker)
    if cached is not None:
        return cached

    if alpaca_client.is_configured():
        try:
            quote = {"price": alpaca_client.get_latest_price(ticker),
                     "source": "alpaca", "as_of": None}
            _cache_put(ticker, quote)
            return quote
        except AlpacaUnavailable as exc:
            logger.info("Alpaca quote failed for %s, trying keyless sources: %s", ticker, exc)

    for name, fetch in (("fmp", _price_from_fmp), ("yahoo", _price_from_yahoo)):
        try:
            quote = fetch(ticker)
        except Exception as exc:
            logger.info("%s quote failed for %s: %s", name, ticker, exc)
            continue
        if quote is not None:
            _cache_put(ticker, quote)
            return quote

    return None

def get_price(conn, ticker: str) -> dict:
    """
    Returns {'price': float, 'source': str, 'as_of': str|None}.
    Raises PriceUnavailable if nothing can price the ticker.
    """
    ticker = ticker.upper()

    quote = _live_quote(ticker)
    if quote is not None:
        return quote

    stored = latest_close_from_db(conn, ticker)
    if stored is not None:
        price, as_of = stored
        return {"price": price, "source": "price_history", "as_of": as_of}

    raise PriceUnavailable(
        f"No price available for {ticker}. Check that the ticker symbol is correct, "
        f"or ingest price history for it first: python ingestion/fetch_prices.py {ticker}"
    )

def get_prices(conn, tickers: list[str]) -> dict[str, dict]:
    """
    Best-effort bulk lookup — tickers that can't be priced are omitted.

    The network legs run concurrently. Done one at a time, a ten-holding
    portfolio paid ten round trips in series on every page load, which is
    the difference between the page feeling instant and feeling broken.
    The DB fallback stays on the calling thread: `conn` is a single
    psycopg2 connection and is not safe to share across threads.
    """
    tickers = list(dict.fromkeys(tickers))
    if not tickers:
        return {}

    quotes: dict[str, dict | None] = {}
    if len(tickers) == 1:
        quotes[tickers[0]] = _live_quote(tickers[0])
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
            for ticker, quote in zip(tickers, pool.map(_live_quote, tickers)):
                quotes[ticker] = quote

    out: dict[str, dict] = {}
    for ticker in tickers:
        quote = quotes.get(ticker)
        if quote is None:
            stored = latest_close_from_db(conn, ticker)
            if stored is None:
                logger.info("No price source for %s; leaving it unpriced", ticker)
                continue
            price, as_of = stored
            quote = {"price": price, "source": "price_history", "as_of": as_of}
        out[ticker] = quote
    return out

def ensure_company(conn, ticker: str, name: str | None = None, sector: str | None = None) -> None:
    """
    Guarantees a `companies` row exists so foreign keys on watchlists,
    holdings and transactions hold. Without this a trade on a
    never-ingested ticker fails with a raw ForeignKeyViolation, which
    surfaces as an opaque 500 *and* aborts the transaction.

    When the caller supplies no name or sector — which is every caller in
    a request path — the row is then enriched from `company_info`. That
    step is best-effort and skips immediately once a row is populated, so
    it costs one primary-key read on the common path and one profile
    lookup the first time a ticker is ever traded or watchlisted.
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

    import company_info

    try:
        company_info.enrich(conn, ticker)
    except Exception as exc:
        logger.info("Company enrichment skipped for %s: %s", ticker, exc)

def company_exists(conn, ticker: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM companies WHERE ticker = %s", (ticker.upper(),))
        return cur.fetchone() is not None
    finally:
        cur.close()
