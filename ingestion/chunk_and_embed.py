"""
chunk_and_embed.py

Takes a cleaned, section-labeled filing (output of clean_filing.py) and:
1. Splits each section into overlapping ~600-token chunks
2. Generates an embedding for each chunk
3. Inserts the filing + chunks into Postgres

Embedding goes through `backend/embeddings.py` rather than calling OpenAI
here. That module decides between OpenAI and a local sentence-transformers
model, and this is the write side of that decision: whatever embedded the
corpus is what must later embed the queries, so the two sides cannot be
allowed to drift apart by each picking their own model. It also means
ingestion works with no OpenAI key at all, which is the point — a corpus
that can't be built keylessly makes the keyless query path moot.

Every chunk records the model that produced it, so a later provider
switch is caught by `embeddings.assert_matches_corpus` with an
explanation rather than by pgvector with a dimension error.

Usage:
    python chunk_and_embed.py data/processed/AAPL_10-K_2025-11-01.json \
        --ticker AAPL --filing-type 10-K --filing-date 2025-11-01 \
        --source-url https://www.sec.gov/...
"""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import embeddings  # noqa: E402

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")

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


def embed_chunks(chunks: list[str]) -> list[list[float]]:
    """
    Embeds a section's chunks as passages.

    Passages, specifically — not queries. The local bge model is
    asymmetric and expects a prefix on the query side only, which is why
    `embeddings` exposes the two separately; using the wrong one here
    would degrade retrieval quietly rather than raise.
    """
    return embeddings.embed_documents(chunks)


def current_embedding_width(cur) -> int | None:
    """The declared width of filing_chunks.embedding, or None if unconstrained."""
    cur.execute(
        """
        SELECT atttypmod
          FROM pg_attribute
         WHERE attrelid = 'filing_chunks'::regclass
           AND attname = 'embedding'
        """
    )
    row = cur.fetchone()
    if row is None or row[0] is None or row[0] < 0:
        return None
    return row[0]


def ensure_embedding_width(cur, dimension: int) -> None:
    """
    Makes the vector column match the active model, while that is free.

    The column width is a property of whichever model embedded the corpus,
    but the model is chosen at runtime by EMBEDDING_PROVIDER, which a .sql
    file can't read. Rather than ship a migration that guesses — and has
    to DELETE the corpus when it guesses wrong — ingestion sets the width
    at the only moment it costs nothing: when there is nothing stored.

    On a populated table this does nothing and says nothing; a genuine
    provider switch is a deliberate TRUNCATE + re-ingest, and
    `assert_matches_corpus` has already refused the mismatch by this point
    with an explanation. So the guard here is not the safety net, it's
    just avoiding a pointless rewrite of a table that is already correct.
    """
    if current_embedding_width(cur) == dimension:
        return

    cur.execute("SELECT EXISTS (SELECT 1 FROM filing_chunks)")
    if cur.fetchone()[0]:
        # Populated and the wrong width. assert_matches_corpus should have
        # caught this; if it didn't, the corpus has vectors with no
        # recorded model, so say what to do rather than ALTER over data.
        raise RuntimeError(
            f"filing_chunks.embedding is not {dimension}-wide but the table "
            f"has rows, so it was built with a different model. Re-embedding "
            f"is unavoidable: TRUNCATE filing_chunks, then re-run this "
            f"ingestion. (Or set EMBEDDING_PROVIDER back to the model that "
            f"built it.)"
        )

    cur.execute(f"ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector({dimension})")
    print(f"  set filing_chunks.embedding width to {dimension}")


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


def insert_chunks(cur, filing_id: int, section_label: str, chunks: list[str],
                  vectors: list[list[float]], start_index: int, model: str) -> int:
    for i, (chunk, embedding) in enumerate(zip(chunks, vectors)):
        cur.execute(
            """
            INSERT INTO filing_chunks
                (filing_id, chunk_text, embedding, section_label, chunk_index, embedding_model)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (filing_id, chunk, embedding, section_label, start_index + i, model),
        )
    return start_index + len(chunks)


def process_filing(processed_json_path: Path, ticker: str, filing_type: str, filing_date: str, source_url: str):
    sections = json.loads(processed_json_path.read_text(encoding="utf-8"))

    model = embeddings.model_name()
    print(f"Embedding with {model} ({embeddings.active_provider()}, "
          f"{embeddings.dimension()} dims)")

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    try:
        # Mixing models within one corpus produces vectors that cannot be
        # compared, so refuse before writing rather than after. The check
        # is here and not per-chunk because it only depends on what is
        # already stored.
        embeddings.assert_matches_corpus(conn)

        # Then make the storage fit the model. On a fresh database this is
        # what lets a keyless clone work with no manual SQL: schema.sql
        # ships the OpenAI width, and this narrows it to the local model's
        # before the first insert.
        ensure_embedding_width(cur, embeddings.dimension())

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
            vectors = embed_chunks(section_chunks)
            chunk_index = insert_chunks(cur, filing_id, section["section_label"],
                                        section_chunks, vectors, chunk_index, model)
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
