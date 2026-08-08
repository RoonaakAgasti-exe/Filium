"""
fetch_transcript.py

Fetch earnings call transcripts and ingest them into the RAG pipeline.

Transcripts are stored as a filing with `filing_type='transcript'` so they
flow through the same chunk → embed → retrieve path as SEC filings, and
`/query` can cite a CEO's answer on an earnings call the same way it cites
Item 1A. schema.sql documents this reuse; this script is the writer.

That reuse is the whole point, so the database work here is delegated to
`chunk_and_embed` rather than reimplemented. It used to be reimplemented,
and every copy had drifted from the schema it was writing to: it looked up
`companies.id` and wrote `filings.company_id`, neither of which exists
(companies is keyed by `ticker`, and filings carries `ticker` too). It also
imported two functions that no module has ever defined, so the file failed
at import and none of the SQL underneath had ever run. Going through the
shared helpers is what stops that drift happening again — and it is also
what makes transcripts record `embedding_model` and respect
`EMBEDDING_PROVIDER`, which a private copy of the insert would have missed.

Usage:
    python ingestion/fetch_transcript.py --ticker AAPL --quarter Q1 --year 2024
    python ingestion/fetch_transcript.py --ticker AAPL --all
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg2
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import embeddings  # noqa: E402
from chunk_and_embed import (  # noqa: E402
    DB_URL,
    chunk_text,
    embed_chunks,
    ensure_company_exists,
    ensure_embedding_width,
    insert_chunks,
    insert_filing,
)
from clean_filing import strip_html  # noqa: E402

SEARCH_URL = "https://www.fool.com/search/?q={query}"

# Transcripts have no filing date of their own the way a 10-K does, but
# `filings.filing_date` is NOT NULL and part of the uniqueness key. Pinning
# each quarter to the 15th of its closing month keeps the four quarters of a
# year distinct and orderable, which is all retrieval needs from the date.
QUARTER_END_MONTH = {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def fetch_transcript_motley_fool(ticker: str, year: int, quarter: str) -> tuple[str, str] | None:
    """
    Best-effort scrape of a Motley Fool transcript. Returns
    (cleaned_text, article_url), or None when nothing matching is found.

    The URL is returned alongside the text, not discarded, because it becomes
    `filings.source_url` — which the UI renders as the citation's href. When
    this returned text only, `ingest_transcript` fell back to a synthetic
    `motley-fool:AAPL:Q1:2024` string, and every transcript citation in the
    app was an unclickable link to a scheme no browser understands.

    None is an ordinary outcome, not an error: transcript URLs are not
    predictable, coverage is uneven, and a future quarter simply doesn't
    exist yet. Callers skip and move on.
    """
    query = f"{ticker} {quarter} {year} earnings call transcript"
    try:
        resp = requests.get(SEARCH_URL.format(query=requests.utils.quote(query)),
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        # Discovery and extraction fail independently, and only discovery is
        # actually broken: fool.com's search page is rendered client-side and
        # now answers a plain GET with 404, while transcript article URLs
        # still serve fine. Saying so points at --url, which works, instead
        # of leaving "search failed" looking like the whole feature is dead.
        print(f"  search failed: {exc}")
        print(f"  fool.com search is JS-rendered and cannot be scraped directly. "
              f"Pass the article URL instead:\n"
              f"    python ingestion/fetch_transcript.py --ticker {ticker} "
              f"--quarter {quarter} --year {year} --url <transcript-url>")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "earnings-call-transcript" not in href:
            continue
        if ticker.lower() not in href.lower():
            continue
        if not href.startswith("http"):
            href = f"https://www.fool.com{href}"
        text = _extract_transcript_text(href)
        return (text, href) if text else None

    print(f"  no transcript link found for {ticker} {quarter} {year}")
    return None


def _extract_transcript_text(url: str) -> str | None:
    """Pulls the transcript body out of a Motley Fool article page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  fetch failed for {url}: {exc}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    article = soup.find("article") or soup.find("div", class_="article-body")
    if article is None:
        print(f"  no article body in {url}")
        return None

    # Strip furniture only. An earlier version also decomposed every <div>,
    # which on a div-structured article body deletes the transcript itself
    # and leaves a few characters of boilerplate — a silent near-empty
    # ingest rather than a visible failure.
    for tag in article.find_all(["aside", "figure", "nav", "form", "footer"]):
        tag.decompose()

    text = strip_html(str(article))
    if len(text) < 500:
        print(f"  extracted only {len(text)} chars from {url} — treating as a miss")
        return None
    return text


def ingest_transcript(ticker: str, year: int, quarter: str, text: str,
                      source_url: str | None = None) -> int:
    """
    Stores one transcript as a filing plus its embedded chunks. Returns the
    filing id.

    Re-ingesting the same quarter replaces its chunks rather than failing on
    the (filing_id, chunk_index) unique constraint, so a re-run after a
    partial fetch — or after switching embedding models — converges instead
    of needing the rows deleted by hand.

    `source_url` should be the real article URL whenever there is one: it is
    stored on the filing and rendered as the href behind every citation this
    transcript produces. The synthetic fallback below exists only for text
    supplied directly (a pasted transcript, a test), where there is genuinely
    no page to link to — it keeps the NOT NULL column satisfied and is
    obviously-not-a-URL rather than a plausible broken one.
    """
    ticker = ticker.upper()
    if quarter not in QUARTER_END_MONTH:
        raise ValueError(f"quarter must be one of {sorted(QUARTER_END_MONTH)}, got {quarter!r}")

    filing_date = date(year, QUARTER_END_MONTH[quarter], 15).isoformat()
    source_url = source_url or f"motley-fool:{ticker}:{quarter}:{year}"
    model = embeddings.model_name()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        # Same two guards process_filing uses, in the same order: refuse a
        # corpus embedded by another model before writing, then size the
        # column to the active one while the table is still empty.
        embeddings.assert_matches_corpus(conn)
        ensure_embedding_width(cur, embeddings.dimension())

        ensure_company_exists(cur, ticker)
        filing_id = insert_filing(cur, ticker, "transcript", filing_date, source_url)

        cur.execute("DELETE FROM filing_chunks WHERE filing_id = %s", (filing_id,))

        # Deliberately not run through split_into_sections: that splits on
        # 10-K "Item N." headers, which a transcript has none of — and which
        # its prose can match by accident, cutting the call into fragments
        # labelled after items it never discusses.
        chunks = chunk_text(text)
        vectors = embed_chunks(chunks)
        insert_chunks(cur, filing_id, f"{quarter} {year} Earnings Call",
                      chunks, vectors, 0, model)

        conn.commit()
        print(f"  ingested filing_id={filing_id} ({len(chunks)} chunks, {model})")
        return filing_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _recent_quarters(limit: int = 8) -> list[tuple[int, str]]:
    """
    The `limit` most recently *closed* quarters, newest first.

    Bounded by today rather than by calendar year: iterating every quarter of
    the current year asks for calls that haven't happened yet, and each miss
    costs two HTTP round trips to discover.
    """
    today = datetime.now()
    quarter_index = (today.month - 1) // 3          # 0-3, the quarter we're in
    year, index = today.year, quarter_index - 1     # step back to the last closed one

    out = []
    while len(out) < limit:
        if index < 0:
            year, index = year - 1, 3
        out.append((year, f"Q{index + 1}"))
        index -= 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch and ingest earnings call transcripts")
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g. AAPL)")
    parser.add_argument("--quarter", help="Q1, Q2, Q3 or Q4")
    parser.add_argument("--year", type=int, help="Calendar year, e.g. 2024")
    parser.add_argument("--all", action="store_true",
                        help="Try the 8 most recently closed quarters")
    # Two explicit sources, because automatic discovery is the one part of
    # this script that depends on a third party's HTML staying still — and
    # it hasn't. Both of these go through the same ingest_transcript path,
    # so a transcript loaded either way is indistinguishable downstream.
    parser.add_argument("--url", help="Ingest this transcript article URL directly, "
                                      "skipping search (requires --quarter and --year)")
    parser.add_argument("--file", help="Ingest transcript text from a local file "
                                       "(requires --quarter and --year)")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    if args.url or args.file:
        if not args.quarter or not args.year:
            parser.error("--quarter and --year are required with --url/--file")
        quarter = args.quarter.upper()

        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
            source_url = None
        else:
            text = _extract_transcript_text(args.url)
            source_url = args.url
            if not text:
                print(f"Could not extract transcript text from {args.url}", file=sys.stderr)
                sys.exit(1)

        print(f"{ticker} {quarter} {args.year}:")
        ingest_transcript(ticker, args.year, quarter, text, source_url=source_url)
        print(f"\nIngested 1 transcript for {ticker}.")
        return

    if args.all:
        targets = _recent_quarters()
    else:
        if not args.quarter or not args.year:
            parser.error("--quarter and --year are required unless --all is used")
        targets = [(args.year, args.quarter.upper())]

    ingested = 0
    failures = 0
    for year, quarter in targets:
        print(f"{ticker} {quarter} {year}:")
        try:
            found = fetch_transcript_motley_fool(ticker, year, quarter)
            if found:
                text, url = found
                ingest_transcript(ticker, year, quarter, text, source_url=url)
                ingested += 1
        except Exception as exc:
            # One bad quarter shouldn't abandon the other seven.
            failures += 1
            print(f"  FAILED — {exc}", file=sys.stderr)

    print(f"\nIngested {ingested} transcript(s) for {ticker}.")
    if ingested:
        print("Ask about them with: python ingestion/answer_query.py "
              f'"..." --ticker {ticker}')

    # Same convention as ingest_filing.py and fetch_prices.py: a partial
    # failure still exits non-zero so a cron wrapper notices. A quarter with
    # no transcript published is not a failure.
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
