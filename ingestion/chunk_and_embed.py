"""
chunk_and_embed.py

Takes a cleaned, section-labeled filing (output of clean_filing.py) and:
1. Splits each section into overlapping ~600-token chunks
2. Generates an embedding for each chunk via OpenAI's embeddings API
3. Inserts the filing + chunks into Postgres

Usage:
    python chunk_and_embed.py data/processed/AAPL_10-K_2025-11-01.json \
        --ticker AAPL --filing-type 10-K --filing-date 2025-11-01 \
        --source-url https://www.sec.gov/...
"""

import argparse
import json
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

CHUNK_SIZE_CHARS = 2400   # roughly ~600 tokens at ~4 chars/token
CHUNK_OVERLAP_CHARS = 350  # roughly 15% overlap


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """
    Splits text into overlapping chunks by character count. Overlap
    matters here because a sentence that explains a key fact can straddle
    a chunk boundary — without overlap you'd lose that context entirely
    for whichever half doesn't get retrieved.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap  # step forward by (chunk_size - overlap)

    return chunks


def embed_chunks(client: OpenAI, chunks: list[str]) -> list[list[float]]:
    """Batches chunks into a single embeddings API call where possible."""
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=chunks)
    return [item.embedding for item in response.data]


def ensure_company_exists(cur, ticker: str):
    cur.execute(
        "INSERT INTO companies (ticker, name) VALUES (%s, %s) "
        "ON CONFLICT (ticker) DO NOTHING",
        (ticker, ticker),  # name backfilled later; ticker is enough to satisfy the FK for now
    )


def insert_filing(cur, ticker: str, filing_type: str, filing_date: str, source_url: str) -> int:
    cur.execute(
        """
        INSERT INTO filings (ticker, filing_type, filing_date, source_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE
            SET source_url = EXCLUDED.source_url
        RETURNING id
        """,
        (ticker, filing_type, filing_date, source_url),
    )
    return cur.fetchone()[0]


def insert_chunks(cur, filing_id: int, section_label: str, chunks: list[str], embeddings: list[list[float]], start_index: int) -> int:
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        cur.execute(
            """
            INSERT INTO filing_chunks (filing_id, chunk_text, embedding, section_label, chunk_index)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (filing_id, chunk, embedding, section_label, start_index + i),
        )
    return start_index + len(chunks)


def process_filing(processed_json_path: Path, ticker: str, filing_type: str, filing_date: str, source_url: str):
    sections = json.loads(processed_json_path.read_text(encoding="utf-8"))

    client = OpenAI()  # reads OPENAI_API_KEY from env
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    try:
        ensure_company_exists(cur, ticker)
        filing_id = insert_filing(cur, ticker, filing_type, filing_date, source_url)
        print(f"Filing row id: {filing_id}")

        chunk_index = 0
        total_chunks = 0

        for section in sections:
            section_chunks = chunk_text(section["text"])
            if not section_chunks:
                continue

            print(f"  {section['section_label'][:50]}: {len(section_chunks)} chunk(s)")
            embeddings = embed_chunks(client, section_chunks)
            chunk_index = insert_chunks(cur, filing_id, section["section_label"], section_chunks, embeddings, chunk_index)
            total_chunks += len(section_chunks)

        conn.commit()
        print(f"Done. Inserted {total_chunks} chunks for {ticker} {filing_type} ({filing_date}).")

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chunk and embed a cleaned filing into Postgres")
    parser.add_argument("processed_file", help="Path to cleaned filing JSON")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--filing-type", required=True)
    parser.add_argument("--filing-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--source-url", required=True)
    args = parser.parse_args()

    process_filing(
        Path(args.processed_file),
        args.ticker,
        args.filing_type,
        args.filing_date,
        args.source_url,
    )
