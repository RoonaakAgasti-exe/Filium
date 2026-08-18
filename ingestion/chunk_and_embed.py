import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import psycopg2
from dotenv import load_dotenv
import embeddings

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
CHUNK_SIZE_CHARS = 2400
CHUNK_OVERLAP_CHARS = 350

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks

def embed_chunks(chunks: list[str]) -> list[list[float]]:
    return embeddings.embed_documents(chunks)

def current_embedding_width(cur) -> int | None:
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
    if current_embedding_width(cur) == dimension:
        return
    cur.execute("SELECT EXISTS (SELECT 1 FROM filing_chunks)")
    if cur.fetchone()[0]:
        raise RuntimeError(
            f"filing_chunks.embedding is not {dimension}-wide but the table "
            f"has rows, so it was built with a different model. Re-embedding "
            f"is unavoidable: TRUNCATE filing_chunks, then re-run this "
            f"ingestion. (Or set EMBEDDING_PROVIDER back to the model that "
            f"built it.)")
    cur.execute(f"ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector({dimension})")
    print(f"  set filing_chunks.embedding width to {dimension}")

def ensure_company_exists(cur, ticker: str):
    cur.execute(
        "INSERT INTO companies (ticker, name) VALUES (%s, %s) "
        "ON CONFLICT (ticker) DO NOTHING", (ticker, ticker))

def insert_filing(cur, ticker: str, filing_type: str, filing_date: str, source_url: str) -> int:
    cur.execute(
        """
        INSERT INTO filings (ticker, filing_type, filing_date, source_url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (ticker, filing_type, filing_date) DO UPDATE
            SET source_url = EXCLUDED.source_url
        RETURNING id
        """, (ticker, filing_type, filing_date, source_url))
    return cur.fetchone()[0]

def insert_chunks(cur, filing_id: int, section_label: str, chunks: list[str], vectors: list[list[float]], start_index: int, model: str) -> int:
    for i, (chunk, embedding) in enumerate(zip(chunks, vectors)):
        cur.execute(
            """
            INSERT INTO filing_chunks
                (filing_id, chunk_text, embedding, section_label, chunk_index, embedding_model)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (filing_id, chunk, embedding, section_label, start_index + i, model))
    return start_index + len(chunks)

def process_filing(processed_json_path: Path, ticker: str, filing_type: str, filing_date: str, source_url: str):
    sections = json.loads(processed_json_path.read_text(encoding = "utf-8"))
    model = embeddings.model_name()
    print(f"Embedding with {model} ({embeddings.active_provider()}, "
          f"{embeddings.dimension()} dims)")
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        embeddings.assert_matches_corpus(conn)
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
            chunk_index = insert_chunks(cur, filing_id, section["section_label"], section_chunks, vectors, chunk_index, model)
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
    parser = argparse.ArgumentParser(description = "Chunk and embed a cleaned filing into Postgres")
    parser.add_argument("processed_file", help = "Path to cleaned filing JSON")
    parser.add_argument("--ticker", required = True)
    parser.add_argument("--filing-type", required = True)
    parser.add_argument("--filing-date", required = True, help = "YYYY-MM-DD")
    parser.add_argument("--source-url", required = True)
    args = parser.parse_args()
    process_filing(Path(args.processed_file), args.ticker, args.filing_type,args.filing_date, args.source_url)