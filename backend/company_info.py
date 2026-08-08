"""
company_info.py

Resolves a ticker's display name and sector, so `companies` holds
something more useful than the ticker repeated back.

Every writer of that table — the price, news and filing ingesters, and
`market_data.ensure_company` — exists to satisfy a foreign key, so each
one inserted `(ticker, ticker, NULL)` and left a comment saying the name
would be "backfilled later". Nothing ever backfilled it. Two features
were quietly living on that placeholder: `/portfolio/analytics` bucketed
every holding into "Unclassified" because `sector` was always NULL, and
every screen that shows a company name showed the ticker twice.

Sources, in the same falls-through order the rest of the app uses:

    1. FMP's profile endpoint  (free key, covers any listed symbol)
    2. A static seed table     (no key, covers the demo universe)

The seed table is not a placeholder for the real thing — it is the tier
that keeps the empty-`.env` promise. Without it a clone with no keys at
all has a sector breakdown that reads "Unclassified: 100%", which looks
like a broken feature rather than an unconfigured one. It is deliberately
small: the tickers this project ships demo data for, plus the benchmark.

Lookups are cached per process and never raise. A company row that keeps
its placeholder name is a cosmetic problem; a trade that 500s because a
profile lookup timed out is not.
"""

import logging
import threading

import requests

import config

logger = logging.getLogger("fincopilot.company_info")

TIMEOUT = 6

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

_SEED_PROFILES: dict[str, tuple[str, str]] = {
    "AAPL": ("Apple Inc.", "Technology"),
    "MSFT": ("Microsoft Corporation", "Technology"),
    "NVDA": ("NVIDIA Corporation", "Technology"),
    "GOOGL": ("Alphabet Inc.", "Communication Services"),
    "GOOG": ("Alphabet Inc.", "Communication Services"),
    "META": ("Meta Platforms, Inc.", "Communication Services"),
    "AMZN": ("Amazon.com, Inc.", "Consumer Cyclical"),
    "TSLA": ("Tesla, Inc.", "Consumer Cyclical"),
    "SPY": ("SPDR S&P 500 ETF Trust", "Financial Services"),
}

def _from_seed(ticker: str) -> dict | None:
    seed = _SEED_PROFILES.get(ticker)
    if seed is None:
        return None
    return {"name": seed[0], "sector": seed[1], "cik": None}

def _from_fmp(ticker: str) -> dict | None:
    """Company profile from FMP. Returns None without a key, or on a miss."""
    if not config.FMP_ENABLED:
        return None

    resp = requests.get(
        "https://financialmodelingprep.com/stable/profile",
        params={"symbol": ticker, "apikey": config.FMP_API_KEY},
        timeout=TIMEOUT,
    )

    payload = resp.json()

    if isinstance(payload, dict):
        raise RuntimeError(
            payload.get("Error Message") or payload.get("message")
            or f"unexpected FMP response (HTTP {resp.status_code})"
        )
    if not payload:
        return None

    row = payload[0]
    name = (row.get("companyName") or "").strip() or None
    sector = (row.get("sector") or "").strip() or None
    cik = (row.get("cik") or "").strip() or None
    if name is None and sector is None:
        return None
    return {"name": name, "sector": sector, "cik": cik}

def fetch_profile(ticker: str) -> dict | None:
    """
    Best-effort {name, sector, cik} for a ticker, or None if no tier knows it.

    Never raises: every caller is enriching a row that already exists and
    is already usable without this.
    """
    ticker = ticker.upper()

    with _cache_lock:
        if ticker in _cache:
            cached = _cache[ticker]
            return dict(cached) if cached else None

    profile = None
    try:
        profile = _from_fmp(ticker)
    except Exception as exc:
        logger.info("FMP profile lookup failed for %s: %s", ticker, exc)

    if profile is None:
        profile = _from_seed(ticker)

    with _cache_lock:
        _cache[ticker] = dict(profile) if profile else {}

    return dict(profile) if profile else None

def clear_cache() -> None:
    """Drops every cached profile. Used by tests."""
    with _cache_lock:
        _cache.clear()

def needs_enrichment(conn, ticker: str) -> bool:
    """
    True when the stored row is still the placeholder the FK-satisfying
    inserts write — no sector, and a name that is just the ticker.

    Checked before going to the network so a populated row costs one
    indexed primary-key read and nothing else.
    """
    ticker = ticker.upper()
    cur = conn.cursor()
    try:
        cur.execute("SELECT name, sector FROM companies WHERE ticker = %s", (ticker,))
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        return True
    name, sector = row
    return not sector or not name or name.strip().upper() == ticker

def enrich(conn, ticker: str) -> bool:
    """
    Fills in a company's name/sector/cik if they're still placeholders.

    Returns True if the row was updated. COALESCE on every column so this
    can never blank out a value a better source already wrote — including
    the `cik` that the SEC ingester sets and neither tier here reports
    reliably.

    Does not commit: callers are already inside a transaction that is
    about to write the thing this row exists to support (a trade, a
    watchlist entry), and enrichment should ride along with it rather than
    committing separately and leaving the two able to disagree.
    """
    ticker = ticker.upper()
    if not needs_enrichment(conn, ticker):
        return False

    profile = fetch_profile(ticker)
    if not profile:
        return False

    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE companies
               SET name   = COALESCE(%s, name),
                   sector = COALESCE(sector, %s),
                   cik    = COALESCE(cik, %s)
             WHERE ticker = %s
            """,
            (profile.get("name"), profile.get("sector"), profile.get("cik"), ticker),
        )
        updated = cur.rowcount > 0
    finally:
        cur.close()

    if updated:
        logger.info("Enriched %s: name=%r sector=%r",
                    ticker, profile.get("name"), profile.get("sector"))
    return updated

def backfill_all(conn) -> dict:
    """
    Enriches every company row that still looks like a placeholder.

    Used by the nightly scheduler and by `python backend/company_info.py`,
    so an existing database picks names and sectors up without anyone
    having to re-ingest anything.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ticker FROM companies "
            "WHERE sector IS NULL OR name IS NULL OR upper(name) = ticker "
            "ORDER BY ticker"
        )
        tickers = [r[0] for r in cur.fetchall()]
    finally:
        cur.close()

    updated = 0
    for ticker in tickers:
        try:
            if enrich(conn, ticker):
                updated += 1
        except Exception:
            logger.exception("Enrichment failed for %s", ticker)
            conn.rollback()

    if updated:
        conn.commit()

    return {"examined": len(tickers), "updated": updated}

if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    import psycopg2

    connection = psycopg2.connect(config.DATABASE_URL)
    try:
        print(backfill_all(connection))
    finally:
        connection.close()
