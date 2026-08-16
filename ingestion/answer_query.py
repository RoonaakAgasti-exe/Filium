import argparse
import os
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv
import embeddings
import rag

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
TOP_K_RETRIEVE = rag.TOP_K_RETRIEVE
TOP_K_FINAL = rag.TOP_K_FINAL

def embed_query(client, query: str) -> list[float]:
    return embeddings.embed_query(query)

def retrieve_chunks(conn, query_embedding: list[float], ticker: str | None, top_k: int) -> list[dict]:
    chunks = rag.retrieve_chunks(conn, query_embedding, [ticker] if ticker else None, top_k)
    return rag._normalise(chunks)

def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    return rag.rerank_chunks(query, chunks, top_k)

def build_prompt(query: str, chunks: list[dict]) -> str:
    return rag.build_prompt(query, chunks)

def extract_citation_ids(answer_text: str) -> set[int]:
    return rag.extract_citation_ids(answer_text)

def answer_query(query: str, ticker: str | None = None) -> dict:
    conn = psycopg2.connect(DB_URL)
    try:
        return rag.answer_query(conn, query, ticker)
    except rag.NoDataError as exc:
        return {"answer": str(exc), "sources": [], "all_sources": [], "generated": False}
    finally:
        conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "Ask a question against ingested filings")
    parser.add_argument("query", help = "Your question")
    parser.add_argument("--ticker", default = None, help = "Restrict search to one ticker")
    args = parser.parse_args()
    result = answer_query(args.query, args.ticker)
    print("\n--- Answer ---")
    print(result["answer"])
    if not result.get("generated", True):
        print("\n(No answer-writing model configured — the above is retrieved "
              "evidence, not a generated answer.)")
    print("\n--- Sources ---")
    for s in result["sources"]:
        print(f"  [{s['marker']}] {s['ticker']} {s['filing_type']} ({s['filing_date']}) — {s['section']}")