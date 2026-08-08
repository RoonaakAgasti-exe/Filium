"""
answer_query.py

CLI front end for the RAG pipeline, and the import surface the eval
harness uses.

This file used to be a second, independent implementation of the whole
pipeline — its own embedding call, its own retrieval SQL, its own prompt,
its own citation parsing — sitting alongside `backend/rag.py`. Two copies
of a retrieval pipeline is a bad idea generally, but it became an actual
bug once embeddings stopped being OpenAI-only: `backend/embeddings.py`
now defaults to a local 384-dimension model when no OpenAI key is set, so
a corpus ingested with the default config could only be queried by code
that went through that module. This file didn't, so it embedded questions
as 1536-dimension OpenAI vectors and every query — including the entire
eval harness, which imports from here — died inside pgvector with
`expected 384 dimensions, not 1536`.

So it is now what rag.py's docstring always claimed it was: a thin
wrapper. The pipeline being measured by eval/ is the pipeline being
served by the API, and there is one place that decides which embedding
model runs.

The function signatures below are kept exactly as they were, including
the unused `client` argument, because eval/judge_faithfulness.py and
eval/label_retrieval.py call them positionally.

Usage:
    python answer_query.py "What did Apple say about supply chain risk?" --ticker AAPL
"""

import argparse
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import embeddings  # noqa: E402
import rag  # noqa: E402

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")

# Re-exported from rag so eval/ measures the same depth the API serves.
TOP_K_RETRIEVE = rag.TOP_K_RETRIEVE
TOP_K_FINAL = rag.TOP_K_FINAL


def embed_query(client, query: str) -> list[float]:
    """
    Embeds a question.

    `client` is ignored — the provider is chosen by backend/embeddings.py,
    which may not be OpenAI at all. It stays in the signature because the
    eval scripts pass one positionally, and because the alternative
    (editing both to drop it) would make this change bigger than the bug
    it fixes.
    """
    return embeddings.embed_query(query)


def retrieve_chunks(conn, query_embedding: list[float], ticker: str | None,
                    top_k: int) -> list[dict]:
    """
    Vector search, delegated to rag.

    Note the ticker argument is a single string here but a list in rag —
    that difference is the reason this shim exists rather than eval
    importing rag directly.
    """
    chunks = rag.retrieve_chunks(
        conn, query_embedding, [ticker] if ticker else None, top_k
    )
    return rag._normalise(chunks)


def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    return rag.rerank_chunks(query, chunks, top_k)


def build_prompt(query: str, chunks: list[dict]) -> str:
    return rag.build_prompt(query, chunks)


def extract_citation_ids(answer_text: str) -> set[int]:
    return rag.extract_citation_ids(answer_text)


def answer_query(query: str, ticker: str | None = None) -> dict:
    """
    Answers one question, opening and closing its own connection.

    Returns rag's response shape, which includes `generated` — False when
    no LLM was configured and `answer` holds the retrieved passages rather
    than written prose. Callers that score answer quality should check it;
    scoring extractive output as though a model wrote it measures nothing.
    """
    conn = psycopg2.connect(DB_URL)
    try:
        return rag.answer_query(conn, query, ticker)
    except rag.NoDataError as exc:
        # The CLI predates the exception and its callers expect a dict.
        return {"answer": str(exc), "sources": [], "all_sources": [], "generated": False}
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ask a question against ingested filings")
    parser.add_argument("query", help="Your question")
    parser.add_argument("--ticker", default=None, help="Restrict search to one ticker")
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
