# market_data.py — Fetches and caches live market prices.
pass

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
    pass

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
    pass
    with _cache_lock:
        _quote_cache.clear()

def _price_from_fmp(ticker: str) -> dict | None:
    pass
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
    pass

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
    pass

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
    pass

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
    pass

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
    pass

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