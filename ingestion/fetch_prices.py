import argparse
import os
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "").strip()
ALPACA_DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets").rstrip("/")
ALPACA_FEED = os.getenv("ALPACA_DATA_FEED", "iex").strip()
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()
REQUEST_TIMEOUT = 30

def _looks_real(value: str) -> bool:
    return bool(value) and not value.lower().startswith("your_")

def fetch_from_alpaca(ticker: str, start: date, end: date) -> list[dict]:
    if not (_looks_real(ALPACA_API_KEY) and _looks_real(ALPACA_SECRET_KEY)):
        raise RuntimeError("Alpaca keys not configured")
    url = f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars"
    headers = {"APCA-API-KEY-ID": ALPACA_API_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}
    params = {"timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(), "adjustment": "split", "feed": ALPACA_FEED, "limit": 10000}
    bars: list[dict] = []
    page_token = None
    while True:
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers = headers, params = params, timeout = REQUEST_TIMEOUT)
        if resp.status_code == 403:
            raise RuntimeError(
                f"Alpaca rejected the request for feed '{ALPACA_FEED}' (403). Free "
                f"accounts only get the IEX feed — set ALPACA_DATA_FEED=iex.")
        resp.raise_for_status()
        payload = resp.json()
        for b in payload.get("bars") or []:
            bars.append({"date":b["t"][:10], "open":b.get("o"), "high":b.get("h"), "low":b.get("l"), "close":b.get("c"), "volume":b.get("v")})
        page_token = payload.get("next_page_token")
        if not page_token:
            break
    if not bars:
        raise RuntimeError(f"Alpaca returned no bars for {ticker}")
    return sorted(bars, key = lambda r: r["date"])

def fetch_from_fmp(ticker: str, start: date, end: date) -> list[dict]:
    if not _looks_real(FMP_API_KEY):
        raise RuntimeError("FMP_API_KEY not configured")
    url = "https://financialmodelingprep.com/stable/historical-price-eod/full"
    params = {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat(), "apikey": FMP_API_KEY}
    resp = requests.get(url, params = params, timeout = REQUEST_TIMEOUT)
    try:
        payload = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"FMP returned a non-JSON response for {ticker}")
    if isinstance(payload, dict):
        message = payload.get("Error Message") or payload.get("message")
        if message:
            raise RuntimeError(f"FMP error (HTTP {resp.status_code}): {message}")
    resp.raise_for_status()
    rows = payload if isinstance(payload, list) else (payload or {}).get("historical") or []
    if not rows:
        raise RuntimeError(f"FMP returned no history for {ticker}")
    return sorted(({"date":r["date"], "open":r.get("open"), "high":r.get("high"), "low":r.get("low"), "close":r.get("close"), "volume":r.get("volume")} for r in rows), key = lambda r: r["date"])

def fetch_from_yahoo(ticker: str, start: date, end: date) -> list[dict]:
    resp = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params = {
            "period1": int(datetime.combine(start, dtime.min, tzinfo = timezone.utc).timestamp()),
            "period2": int(datetime.combine(end, dtime.max, tzinfo = timezone.utc).timestamp()),
            "interval": "1d"},
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Filium/1.0)"},
        timeout = REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(f"Yahoo error: {chart['error'].get('description', chart['error'])}")
    results = chart.get("result") or []
    if not results:
        raise RuntimeError(f"Yahoo returned no result for {ticker}")
    result = results[0]
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    if not timestamps:
        raise RuntimeError(f"Yahoo returned no bars for {ticker}")
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    rows = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(ts, tz = timezone.utc).date().isoformat(),
            "open": _to_float(opens[i] if i < len(opens) else None),
            "high": _to_float(highs[i] if i < len(highs) else None),
            "low": _to_float(lows[i] if i < len(lows) else None),
            "close": _to_float(close),
            "volume": _to_int(volumes[i] if i < len(volumes) else None)})
    if not rows:
        raise RuntimeError(f"Yahoo returned only empty bars for {ticker}")
    return sorted(rows, key = lambda r: r["date"])

def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
    
def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None

SOURCES = {"alpaca": fetch_from_alpaca, "fmp": fetch_from_fmp, "yahoo": fetch_from_yahoo}
SOURCE_ORDER = ["alpaca", "fmp", "yahoo"]

def fetch_bars(ticker: str, start: date, end: date, source: str = "auto") -> tuple[list[dict], str]:
    order = SOURCE_ORDER if source == "auto" else [source]
    problems = []
    for name in order:
        try:
            bars = SOURCES[name](ticker, start, end)
            return bars, name
        except Exception as exc:
            problems.append(f"  {name}: {exc}")
    raise RuntimeError(f"No source could supply price history for {ticker}:\n" + "\n".join(problems))

def ensure_company(cur, ticker: str) -> None:
    cur.execute("INSERT INTO companies (ticker, name) VALUES (%s, %s) ON CONFLICT (ticker) DO NOTHING", (ticker, ticker))

def store_bars(conn, ticker: str, bars: list[dict]) -> int:
    cur = conn.cursor()
    try:
        ensure_company(cur, ticker)
        cur.executemany(
            """
            INSERT INTO price_history (ticker, date, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
            """, [(ticker, b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in bars])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return len(bars)

def watchlisted_tickers(conn) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT ticker FROM watchlists ORDER BY ticker")
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()

def fetch_and_store(conn, ticker: str, days: int, source: str = "auto") -> dict:
    ticker = ticker.upper()
    end = date.today()
    start = end - timedelta(days = days)
    bars, used = fetch_bars(ticker, start, end, source)
    if not bars:
        return {"ticker":ticker, "source":used, "rows":0, "first_date":None, "last_date":None}
    stored = store_bars(conn, ticker, bars)
    return {"ticker":ticker, "source":used, "rows":stored, "first_date":bars[0]["date"], "last_date":bars[-1]["date"]}

def main():
    parser = argparse.ArgumentParser(description = "Fetch daily OHLCV bars into price_history")
    parser.add_argument("tickers", nargs = "*", help = "One or more ticker symbols")
    parser.add_argument("--days", type = int, default = 730, help = "Calendar days of history to request (default: 730)")
    parser.add_argument("--source", choices = ["auto", *SOURCE_ORDER], default = "auto", help = "Force a specific source instead of trying each in turn")
    parser.add_argument("--watchlisted", action = "store_true", help = "Fetch every ticker on any user's watchlist")
    args = parser.parse_args()
    conn = psycopg2.connect(DB_URL)
    try:
        tickers = [t.upper() for t in args.tickers]
        if args.watchlisted:
            tickers = sorted(set(tickers) | set(watchlisted_tickers(conn)))
        if not tickers:
            parser.error("Give at least one ticker, or pass --watchlisted")
        failures = 0
        for ticker in tickers:
            try:
                result = fetch_and_store(conn, ticker, args.days, args.source)
                if result["rows"] == 0:
                    print(f"{result['ticker']}: no bars returned by {result['source']} "
                          f"for the last {args.days} day(s) — nothing new to store")
                else:
                    print(
                        f"{result['ticker']}: {result['rows']} bars via {result['source']} "
                        f"({result['first_date']} -> {result['last_date']})")
            except Exception as exc:
                failures += 1
                print(f"{ticker}: FAILED\n{exc}", file = sys.stderr)
        if failures:
            sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()