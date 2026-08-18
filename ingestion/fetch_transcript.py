import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import psycopg2
import requests
from bs4 import BeautifulSoup
import embeddings
from chunk_and_embed import (DB_URL, chunk_text, embed_chunks, ensure_company_exists, ensure_embedding_width, insert_chunks, insert_filing)
from clean_filing import strip_html
SEARCH_URL = "https://www.fool.com/search/?q={query}"
QUARTER_END_MONTH = {"Q1":3, "Q2":6, "Q3":9, "Q4":12}
HEADERS = {"User-Agent":(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")}

def fetch_transcript_motley_fool(ticker: str, year: int, quarter: str) -> tuple[str, str] | None:
    query = f"{ticker} {quarter} {year} earnings call transcript"
    try:
        resp = requests.get(SEARCH_URL.format(query = requests.utils.quote(query)), headers = HEADERS, timeout = 15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  search failed: {exc}")
        print(f"  fool.com search is JS-rendered and cannot be scraped directly. "
              f"Pass the article URL instead:\n"
              f"    python ingestion/fetch_transcript.py --ticker {ticker} "
              f"--quarter {quarter} --year {year} --url <transcript-url>")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    for anchor in soup.find_all("a", href = True):
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
    try:
        resp = requests.get(url, headers = HEADERS, timeout = 15)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  fetch failed for {url}: {exc}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    article = soup.find("article") or soup.find("div", class_ = "article-body")
    if article is None:
        print(f"  no article body in {url}")
        return None
    for tag in article.find_all(["aside", "figure", "nav", "form", "footer"]):
        tag.decompose()
    text = strip_html(str(article))
    if len(text) < 500:
        print(f"  extracted only {len(text)} chars from {url} — treating as a miss")
        return None
    return text

def ingest_transcript(ticker: str, year: int, quarter: str, text: str, source_url: str | None = None) -> int:
    ticker = ticker.upper()
    if quarter not in QUARTER_END_MONTH:
        raise ValueError(f"quarter must be one of {sorted(QUARTER_END_MONTH)}, got {quarter!r}")
    filing_date = date(year, QUARTER_END_MONTH[quarter], 15).isoformat()
    source_url = source_url or f"motley-fool:{ticker}:{quarter}:{year}"
    model = embeddings.model_name()
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        embeddings.assert_matches_corpus(conn)
        ensure_embedding_width(cur, embeddings.dimension())
        ensure_company_exists(cur, ticker)
        filing_id = insert_filing(cur, ticker, "transcript", filing_date, source_url)
        cur.execute("DELETE FROM filing_chunks WHERE filing_id = %s", (filing_id,))
        chunks = chunk_text(text)
        vectors = embed_chunks(chunks)
        insert_chunks(cur, filing_id, f"{quarter} {year} Earnings Call", chunks, vectors, 0, model)
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
    today = datetime.now()
    quarter_index = (today.month - 1) // 3
    year, index = today.year, quarter_index - 1
    out = []
    while len(out) < limit:
        if index < 0:
            year, index = year - 1, 3
        out.append((year, f"Q{index + 1}"))
        index -= 1
    return out

def main() -> None:
    parser = argparse.ArgumentParser(description = "Fetch and ingest earnings call transcripts")
    parser.add_argument("--ticker", required = True, help = "Stock ticker (e.g. AAPL)")
    parser.add_argument("--quarter", help = "Q1, Q2, Q3 or Q4")
    parser.add_argument("--year", type = int, help = "Calendar year, e.g. 2024")
    parser.add_argument("--all", action = "store_true", help = "Try the 8 most recently closed quarters")
    parser.add_argument("--url", help = "Ingest this transcript article URL directly skipping search (requires --quarter and --year)")
    parser.add_argument("--file", help = "Ingest transcript text from a local file (requires --quarter and --year)")
    args = parser.parse_args()
    ticker = args.ticker.upper()
    if args.url or args.file:
        if not args.quarter or not args.year:
            parser.error("--quarter and --year are required with --url/--file")
        quarter = args.quarter.upper()
        if args.file:
            text = Path(args.file).read_text(encoding = "utf-8")
            source_url = None
        else:
            text = _extract_transcript_text(args.url)
            source_url = args.url
            if not text:
                print(f"Could not extract transcript text from {args.url}", file = sys.stderr)
                sys.exit(1)
        print(f"{ticker} {quarter} {args.year}:")
        ingest_transcript(ticker, args.year, quarter, text, source_url = source_url)
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
                ingest_transcript(ticker, year, quarter, text, source_url = url)
                ingested += 1
        except Exception as exc:
            failures += 1
            print(f"  FAILED — {exc}", file = sys.stderr)
    print(f"\nIngested {ingested} transcript(s) for {ticker}.")
    if ingested:
        print(f'Ask about them with: python ingestion/answer_query.py "..." --ticker {ticker}')
    if failures:
        sys.exit(1)

if __name__ == "__main__":
    main()