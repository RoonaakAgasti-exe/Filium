"""
fetch_filing.py

Downloads the most recent 10-K (annual report) for a given ticker from
SEC EDGAR's free, public API. No API key needed — SEC only requires a
descriptive User-Agent header identifying who's making the request.

Usage:
    python fetch_filing.py AAPL
    python fetch_filing.py AAPL --filing-type 10-Q --count 2
"""

import argparse
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

EDGAR_USER_AGENT = os.getenv("EDGAR_USER_AGENT", "FinCopilot dev dev@example.com")
HEADERS = {"User-Agent": EDGAR_USER_AGENT}

RAW_DATA_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

TICKER_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def get_cik_for_ticker(ticker: str) -> str:
    """
    SEC EDGAR indexes filings by CIK (Central Index Key), not ticker symbol.
    This pulls their ticker->CIK lookup table and finds the match.
    """
    resp = requests.get(TICKER_CIK_MAP_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    ticker = ticker.upper()
    for entry in data.values():
        if entry["ticker"] == ticker:
            # CIK numbers need to be zero-padded to 10 digits for the next API call
            return str(entry["cik_str"]).zfill(10)

    raise ValueError(f"No CIK found for ticker '{ticker}'")


def get_filing_index(cik: str, filing_type: str = "10-K", count: int = 1) -> list[dict]:
    """
    Given a CIK, fetches the company's recent filings metadata and returns
    the URLs + dates of the most recent filings matching filing_type.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    recent = data["filings"]["recent"]
    matches = []

    for i, form in enumerate(recent["form"]):
        if form == filing_type:
            accession_number = recent["accessionNumber"][i].replace("-", "")
            primary_doc = recent["primaryDocument"][i]
            filing_date = recent["filingDate"][i]

            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{accession_number}/{primary_doc}"
            )
            matches.append({
                "filing_date": filing_date,
                "accession_number": accession_number,
                "url": doc_url,
            })

        if len(matches) >= count:
            break

    if not matches:
        raise ValueError(f"No {filing_type} filings found for CIK {cik}")

    return matches


def download_filing(filing: dict, ticker: str, filing_type: str) -> Path:
    """Downloads the raw filing HTML and saves it to data/raw/."""
    resp = requests.get(filing["url"], headers=HEADERS, timeout=30)
    resp.raise_for_status()

    filename = f"{ticker}_{filing_type}_{filing['filing_date']}.html"
    out_path = RAW_DATA_DIR / filename
    out_path.write_text(resp.text, encoding="utf-8")

    return out_path


def fetch_filings_for_ticker(ticker: str, filing_type: str = "10-K", count: int = 1) -> list[Path]:
    print(f"Looking up CIK for {ticker}...")
    cik = get_cik_for_ticker(ticker)
    print(f"  CIK: {cik}")

    print(f"Fetching {filing_type} filing index (count={count})...")
    filings = get_filing_index(cik, filing_type, count)

    saved_paths = []
    for filing in filings:
        print(f"  Downloading filing from {filing['filing_date']}...")
        path = download_filing(filing, ticker, filing_type)
        saved_paths.append(path)
        print(f"  Saved to {path}")
        time.sleep(0.2)  # be polite to SEC's servers — they do rate limit aggressively

    return saved_paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download SEC filings for a ticker")
    parser.add_argument("ticker", help="Stock ticker, e.g. AAPL")
    parser.add_argument("--filing-type", default="10-K", help="10-K, 10-Q, 8-K, etc.")
    parser.add_argument("--count", type=int, default=1, help="How many recent filings to pull")
    args = parser.parse_args()

    fetch_filings_for_ticker(args.ticker, args.filing_type, args.count)
