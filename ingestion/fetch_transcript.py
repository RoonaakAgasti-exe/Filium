"""
Fetch earnings call transcripts and ingest them into the RAG pipeline.

Transcripts are treated as a special filing type ('transcript') so they flow
through the same clean → chunk → embed pipeline as SEC filings.

Usage:
    python fetch_transcript.py --ticker AAPL --quarter Q1 --year 2024
    python fetch_transcript.py --ticker AAPL --all
"""

import argparse
import os
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clean_filing import clean_text
from chunk_and_embed import chunk_and_embed_filing

# Motley Fool hosts free earnings call transcripts
MOTLEY_FOOL_URL = "https://www.fool.com/earnings/call-transcripts/{year}/{quarter}/{ticker}-{slug}-earnings-call-transcript/"


def fetch_transcript_motley_fool(ticker: str, year: int, quarter: str) -> str | None:
    """
    Attempt to fetch a transcript from Motley Fool.
    Returns the cleaned transcript text or None if not found.
    """
    # Motley Fool URL slugs are unpredictable; try a search-based approach
    search_url = f"https://www.fool.com/search/solr.aspx?q={ticker}+{quarter}+{year}+earnings+call+transcript"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(search_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the first transcript link
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "earnings-call-transcript" in href and ticker.lower() in href.lower():
                if not href.startswith("http"):
                    href = f"https://www.fool.com{href}"
                return _extract_transcript_text(href, headers)

        print(f"No transcript found for {ticker} {quarter} {year}")
        return None

    except Exception as e:
        print(f"Error fetching transcript: {e}")
        return None


def _extract_transcript_text(url: str, headers: dict) -> str | None:
    """Extract the main transcript body from a Motley Fool article page."""
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # The transcript is usually in the article body
        article = soup.find("article") or soup.find("div", class_="article-body")
        if not article:
            return None

        # Remove ads, related links, etc.
        for tag in article.find_all(["aside", "script", "style", "div", "figure"]):
            tag.decompose()

        text = article.get_text(separator="\n", strip=True)
        return clean_text(text)

    except Exception as e:
        print(f"Error extracting transcript: {e}")
        return None


def ingest_transcript(ticker: str, year: int, quarter: str, text: str) -> int:
    """
    Ingest transcript text into the database as a filing with type='transcript'.
    Returns the filing_id.
    """
    import psycopg2
    from psycopg2.extras import RealDictCursor

    db_url = os.getenv("DATABASE_URL", "postgresql://fincopilot:fincopilot@localhost:5432/fincopilot")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Ensure company exists
    cur.execute("SELECT id FROM companies WHERE ticker = %s", (ticker,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO companies (ticker, name) VALUES (%s, %s) RETURNING id",
            (ticker, ticker),
        )
        company_id = cur.fetchone()["id"]
    else:
        company_id = row["id"]

    # Create filing record
    filing_date = datetime(year, {"Q1": 3, "Q2": 6, "Q3": 9, "Q4": 12}[quarter], 15)
    cur.execute(
        """
        INSERT INTO filings (company_id, filing_type, filing_date, raw_text, source_url)
        VALUES (%s, 'transcript', %s, %s, %s)
        RETURNING id
        """,
        (company_id, filing_date, text, f"motley-fool-{ticker}-{quarter}-{year}"),
    )
    filing_id = cur.fetchone()["id"]
    conn.commit()

    # Chunk and embed
    chunk_and_embed_filing(filing_id, text, conn)

    cur.close()
    conn.close()
    return filing_id


def main():
    parser = argparse.ArgumentParser(description="Fetch and ingest earnings call transcripts")
    parser.add_argument("--ticker", required=True, help="Stock ticker (e.g., AAPL)")
    parser.add_argument("--quarter", help="Quarter (Q1, Q2, Q3, Q4)")
    parser.add_argument("--year", type=int, help="Year (e.g., 2024)")
    parser.add_argument("--all", action="store_true", help="Fetch all available transcripts")
    args = parser.parse_args()

    ticker = args.ticker.upper()

    if args.all:
        # Try last 8 quarters
        current_year = datetime.now().year
        quarters = ["Q1", "Q2", "Q3", "Q4"]
        for year in range(current_year - 2, current_year + 1):
            for q in quarters:
                print(f"Fetching {ticker} {q} {year}...")
                text = fetch_transcript_motley_fool(ticker, year, q)
                if text:
                    fid = ingest_transcript(ticker, year, q, text)
                    print(f"  ✓ Ingested filing_id={fid}")
                else:
                    print(f"  ✗ Not found")
    else:
        if not args.quarter or not args.year:
            parser.error("--quarter and --year are required unless --all is used")
        text = fetch_transcript_motley_fool(ticker, args.year, args.quarter)
        if text:
            fid = ingest_transcript(ticker, args.year, args.quarter, text)
            print(f"✓ Ingested filing_id={fid}")
        else:
            print("✗ Transcript not found")
            sys.exit(1)


if __name__ == "__main__":
    main()
