import argparse
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()
REQUEST_TIMEOUT = 30
MAX_HEADLINE_CHARS = 500
MAX_SUMMARY_CHARS = 2000
FINNHUB_WINDOW_DAYS = 7
FINNHUB_PAGE_CAP = 230
FINNHUB_MIN_WINDOW_DAYS = 1
FINNHUB_REQUEST_PAUSE = 1.1

def _looks_real(value: str) -> bool:
    return bool(value) and not value.lower().startswith("your_")

def _clean(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    collapsed = " ".join(text.split())
    return collapsed[:limit] or None

def _finnhub_window(ticker: str, start: date, end: date) -> list[dict]:
    resp = requests.get("https://finnhub.io/api/v1/company-news", params = {"symbol": ticker, "from": start.isoformat(), "to": end.isoformat(), "token": FINNHUB_API_KEY}, timeout = REQUEST_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected Finnhub response: {str(payload)[:200]}")
    articles = []
    for a in payload:
        headline = _clean(a.get("headline"), MAX_HEADLINE_CHARS)
        ts = a.get("datetime")
        if not headline or not ts:
            continue
        articles.append({
            "published_date": datetime.fromtimestamp(ts, tz = timezone.utc).date(),
            "headline": headline,
            "summary": _clean(a.get("summary"), MAX_SUMMARY_CHARS),
            "url": a.get("url"),
            "source": a.get("source"),
        })
    return articles

def fetch_from_finnhub(ticker: str, start: date, end: date) -> list[dict]:
    if not _looks_real(FINNHUB_API_KEY):
        raise RuntimeError("FINNHUB_API_KEY not configured")
    collected: dict[tuple[date, str], dict] = {}
    truncated_days = 0
    def collect(win_start: date, win_end: date) -> None:
        nonlocal truncated_days
        time.sleep(FINNHUB_REQUEST_PAUSE)
        articles = _finnhub_window(ticker, win_start, win_end)
        span_days = (win_end - win_start).days + 1
        if len(articles) >= FINNHUB_PAGE_CAP and span_days > FINNHUB_MIN_WINDOW_DAYS:
            midpoint = win_start + timedelta(days = span_days // 2)
            collect(win_start, midpoint - timedelta(days = 1))
            collect(midpoint, win_end)
            return
        if len(articles) >= FINNHUB_PAGE_CAP:
            truncated_days += 1
        for article in articles:
            collected.setdefault((article["published_date"], article["headline"]), article)
    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days = FINNHUB_WINDOW_DAYS - 1), end)
        collect(window_start, window_end)
        window_start = window_end + timedelta(days = 1)
    if truncated_days:
        print(f"  note: {truncated_days} day(s) hit Finnhub's per-call cap; "
              f"those days are sampled, not complete")
    if not collected:
        raise RuntimeError(f"Finnhub returned no articles for {ticker}")
    return sorted(collected.values(), key = lambda a: a["published_date"])

def fetch_from_newsapi(ticker: str, start: date, end: date) -> list[dict]:
    if not _looks_real(NEWS_API_KEY):
        raise RuntimeError("NEWS_API_KEY not configured")
    resp = requests.get("https://newsapi.org/v2/everything", params = {"q": f'"{ticker}" AND (stock OR shares OR earnings)', "from": start.isoformat(), "to": end.isoformat(), "language": "en", "sortBy": "publishedAt", "pageSize": 100, "apiKey": NEWS_API_KEY}, timeout = REQUEST_TIMEOUT)
    payload = resp.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"NewsAPI error: {payload.get('message', resp.status_code)}")
    rows = payload.get("articles") or []
    if not rows:
        raise RuntimeError(f"NewsAPI returned no articles for {ticker}")
    articles = []
    for a in rows:
        headline = _clean(a.get("title"), MAX_HEADLINE_CHARS)
        published = a.get("publishedAt")
        if not headline or not published:
            continue
        articles.append({
            "published_date": datetime.fromisoformat(published.replace("Z", "+00:00")).date(),
            "headline": headline,
            "summary": _clean(a.get("description"), MAX_SUMMARY_CHARS),
            "url": a.get("url"),
            "source": (a.get("source") or {}).get("name")
        })
    return articles

def fetch_from_rss(ticker: str, start: date, end: date) -> list[dict]:
    resp = requests.get("https://news.google.com/rss/search", params = {"q": f"{ticker} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"}, headers = {"User-Agent": "Filium/1.0 (news ingestion)"}, timeout = REQUEST_TIMEOUT)
    resp.raise_for_status()
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise RuntimeError(f"Google News RSS returned unparseable XML: {exc}")
    articles = []
    for item in root.iterfind(".//item"):
        title = _clean(item.findtext("title"), MAX_HEADLINE_CHARS)
        pub = item.findtext("pubDate")
        if not title or not pub:
            continue
        try:
            published = parsedate_to_datetime(pub).date()
        except (TypeError, ValueError):
            continue
        if not (start <= published <= end):
            continue
        source_el = item.find("source")
        publisher = (source_el.text if source_el is not None else None)
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -(len(publisher) + 3)]
        articles.append({
            "published_date": published,
            "headline": title,
            "summary": _clean(item.findtext("description"), MAX_SUMMARY_CHARS),
            "url": item.findtext("link"),
            "source": publisher
        })
    if not articles:
        raise RuntimeError(f"Google News RSS had no in-range items for {ticker}")
    return articles

SOURCES = {"finnhub": fetch_from_finnhub, "newsapi": fetch_from_newsapi, "rss": fetch_from_rss}
SOURCE_ORDER = ["finnhub", "newsapi", "rss"]

def fetch_articles(ticker: str, start: date, end: date, source: str = "auto") -> tuple[list[dict], str]:
    order = SOURCE_ORDER if source == "auto" else [source]
    problems = []
    for name in order:
        try:
            return SOURCES[name](ticker, start, end), name
        except Exception as exc:
            problems.append(f"  {name}: {exc}")
    raise RuntimeError(f"No source could supply news for {ticker}:\n" + "\n".join(problems))

def ensure_company(cur, ticker: str) -> None:
    cur.execute("INSERT INTO companies (ticker, name) VALUES (%s, %s) ON CONFLICT (ticker) DO NOTHING", (ticker, ticker))

def store_articles(conn, ticker: str, articles: list[dict]) -> int:
    if not articles:
        return 0
    cur = conn.cursor()
    try:
        ensure_company(cur, ticker)
        before = _count(cur, ticker)
        cur.executemany(
            """
            INSERT INTO news_articles
                (ticker, published_date, headline, summary, url, source)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, published_date, headline) DO NOTHING
            """, [(ticker, a["published_date"], a["headline"], a["summary"], a["url"], a["source"]) for a in articles])
        after = _count(cur, ticker)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return after - before

def _count(cur, ticker: str) -> int:
    cur.execute("SELECT count(*) FROM news_articles WHERE ticker = %s", (ticker,))
    return cur.fetchone()[0]

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
    articles, used = fetch_articles(ticker, start, end, source)
    inserted = store_articles(conn, ticker, articles)
    dates = [a["published_date"] for a in articles]
    return {"ticker": ticker, "source": used, "fetched": len(articles), "inserted": inserted, "first_date": min(dates).isoformat() if dates else None, "last_date": max(dates).isoformat() if dates else None}

def main():
    parser = argparse.ArgumentParser(description = "Fetch news headlines into news_articles")
    parser.add_argument("tickers", nargs = "*", help = "One or more ticker symbols")
    parser.add_argument("--days", type = int, default = 90,
                        help = "How far back to request (default: 90)")
    parser.add_argument("--source", choices = ["auto", *SOURCE_ORDER], default = "auto")
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
                r = fetch_and_store(conn, ticker, args.days, args.source)
                print(
                    f"{r['ticker']}: {r['inserted']} new of {r['fetched']} fetched "
                    f"via {r['source']} ({r['first_date']} -> {r['last_date']})")
            except Exception as exc:
                failures += 1
                print(f"{ticker}: FAILED\n{exc}", file = sys.stderr)
        print("\nNext: score them —  python ml/sentiment.py --ticker <TICKER> --source news")
        if failures:
            sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()