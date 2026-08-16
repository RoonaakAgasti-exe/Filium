import argparse
import sys
import time
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
from chunk_and_embed import DB_URL, process_filing
from clean_filing import clean_filing
from fetch_filing import (download_filing, get_cik_for_ticker, get_filing_index)

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv()

def already_ingested(ticker:str, filing_type:str, filing_date:str) -> bool:
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM filings WHERE ticker = %s AND filing_type = %s "
                "AND filing_date = %s", (ticker.upper(), filing_type, filing_date))
            return cur.fetchone() is not None
        finally:
            cur.close()
    finally:
        conn.close()

def ingest_one(ticker:str, filing_type:str, count:int, skip_existing:bool) -> int:
    ticker = ticker.upper()
    print(f"\n=== {ticker} {filing_type} ===")
    cik = get_cik_for_ticker(ticker)
    filings = get_filing_index(cik, filing_type, count)
    embedded = 0
    for filing in filings:
        filing_date = filing["filing_date"]
        if skip_existing and already_ingested(ticker, filing_type, filing_date):
            print(f"  {filing_date}: already in the database — skipping")
            continue
        source_url = filing["url"]
        print(f"  {filing_date}: downloading")
        raw_path = download_filing(filing, ticker, filing_type)
        processed_path = clean_filing(raw_path)
        process_filing(processed_path, ticker, filing_type, filing_date, source_url)
        embedded += 1
        time.sleep(0.2)
    return embedded

def main() -> None:
    parser = argparse.ArgumentParser(description = "Fetch, clean, and embed SEC filings for one or more tickers")
    parser.add_argument("tickers", nargs = "+", help = "One or more ticker symbols")
    parser.add_argument("--filing-type", default = "10-K", help = "10-K, 10-Q, 8-K (default: 10-K)")
    parser.add_argument("--count", type = int, default = 1, help = "How many recent filings per ticker (default: 1)")
    parser.add_argument("--skip-existing", action = "store_true", help = "Skip filings already in the database instead of re-embedding them. Re-embedding costs OpenAI credits for no new information.")
    args = parser.parse_args()
    total = 0
    failures = 0
    for ticker in args.tickers:
        try:
            total += ingest_one(ticker, args.filing_type, args.count, args.skip_existing)
        except Exception as exc:
            failures += 1
            print(f"  {ticker.upper()}: FAILED — {exc}", file=sys.stderr)
    print(f"\nEmbedded {total} filing(s) across {len(args.tickers)} ticker(s).")
    if failures:
        sys.exit(1)

if __name__ == "__main__":
    main()