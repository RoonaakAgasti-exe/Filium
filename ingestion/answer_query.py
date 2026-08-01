"""
answer_query.py

The core RAG loop:
1. Embed the user's question
2. Retrieve the most relevant filing chunks via pgvector cosine similarity
3. (Optional) rerank with a cross-encoder for better precision
4. Hand the retrieved chunks + question to an LLM, forcing it to answer
   only from what was retrieved and to cite which chunk(s) it used
5. Map the LLM's citation markers back to real chunk IDs + source filings

Usage:
    python answer_query.py "What did Apple say about supply chain risk?" --ticker AAPL
"""

import argparse
import os
import re

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
EMBEDDING_MODEL = "text-embedding-3-small"
LLM_MODEL = "gpt-4o-mini"
TOP_K_RETRIEVE = 8   # how many chunks to pull from the DB before reranking
TOP_K_FINAL = 4      # how many chunks actually go into the LLM prompt

SYSTEM_PROMPT = """You are a financial research assistant. You will be given
excerpts from a company's SEC filing, each labeled with a source number like [1], [2].

Rules:
- Answer ONLY using information present in the excerpts below.
- If the excerpts don't contain the answer, say so plainly — do not guess or use outside knowledge.
- Every claim you make must end with the source number(s) it came from, like this: "...impacted margins [2]."
- Keep the answer concise and directly responsive to the question.
"""


def embed_query(client: OpenAI, query: str) -> list[float]:
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    return response.data[0].embedding


def retrieve_chunks(conn, query_embedding: list[float], ticker: str | None, top_k: int) -> list[dict]:
    """
    Exact cosine-similarity search over filing_chunks, optionally scoped to
    one ticker. Note: no ivfflat index is assumed here — see the note in
    schema.sql about why that index is deliberately deferred until the
    table has real volume. Exact search is correct and fast enough at
    this project's data scale.
    """
    cur = conn.cursor()

    if ticker:
        cur.execute(
            """
            SELECT fc.id, fc.chunk_text, fc.section_label, f.ticker, f.filing_type,
                   f.filing_date, f.source_url, fc.embedding <=> %s::vector AS distance
            FROM filing_chunks fc
            JOIN filings f ON f.id = fc.filing_id
            WHERE f.ticker = %s
            ORDER BY distance
            LIMIT %s
            """,
            (query_embedding, ticker, top_k),
        )
    else:
        cur.execute(
            """
            SELECT fc.id, fc.chunk_text, fc.section_label, f.ticker, f.filing_type,
                   f.filing_date, f.source_url, fc.embedding <=> %s::vector AS distance
            FROM filing_chunks fc
            JOIN filings f ON f.id = fc.filing_id
            ORDER BY distance
            LIMIT %s
            """,
            (query_embedding, top_k),
        )

    columns = ["chunk_id", "chunk_text", "section_label", "ticker", "filing_type",
               "filing_date", "source_url", "distance"]
    rows = cur.fetchall()
    cur.close()

    return [dict(zip(columns, row)) for row in rows]


def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """
    Optional cross-encoder reranking step. Cross-encoders score a
    (query, chunk) pair jointly rather than comparing precomputed vectors,
    which usually gives noticeably better precision than raw embedding
    similarity — at the cost of being slower, so it only runs on the
    already-narrowed candidate set from retrieve_chunks, not the whole table.

    Requires: pip install sentence-transformers
    Falls back to the embedding-similarity order if the reranker isn't
    installed, so the pipeline still works without it.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        print("  [rerank skipped: sentence-transformers not installed]")
        return chunks[:top_k]

    model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    pairs = [(query, c["chunk_text"]) for c in chunks]
    scores = model.predict(pairs)

    scored = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:top_k]]


def build_prompt(query: str, chunks: list[dict]) -> str:
    excerpt_blocks = []
    for i, c in enumerate(chunks, start=1):
        excerpt_blocks.append(
            f"[{i}] (from {c['ticker']} {c['filing_type']}, {c['filing_date']}, "
            f"section: {c['section_label']})\n{c['chunk_text']}"
        )

    excerpts_text = "\n\n".join(excerpt_blocks)
    return f"Excerpts:\n\n{excerpts_text}\n\nQuestion: {query}"


def extract_citation_ids(answer_text: str) -> set[int]:
    """Pulls out every [N] marker the LLM used, e.g. '[1]' and '[2, 3]'."""
    matches = re.findall(r"\[(\d+(?:,\s*\d+)*)\]", answer_text)
    ids = set()
    for m in matches:
        for part in m.split(","):
            ids.add(int(part.strip()))
    return ids


def answer_query(query: str, ticker: str | None = None) -> dict:
    client = OpenAI()
    conn = psycopg2.connect(DB_URL)

    try:
        query_embedding = embed_query(client, query)
        candidates = retrieve_chunks(conn, query_embedding, ticker, TOP_K_RETRIEVE)

        if not candidates:
            return {"answer": "No filing data found for this query.", "sources": []}

        top_chunks = rerank_chunks(query, candidates, TOP_K_FINAL)
        prompt = build_prompt(query, top_chunks)

        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        answer_text = response.choices[0].message.content

        cited_ids = extract_citation_ids(answer_text)
        sources = [
            {
                "marker": i,
                "ticker": c["ticker"],
                "filing_type": c["filing_type"],
                "filing_date": str(c["filing_date"]),
                "section": c["section_label"],
                "source_url": c["source_url"],
                "chunk_id": c["chunk_id"],
            }
            for i, c in enumerate(top_chunks, start=1)
            if i in cited_ids
        ]

        return {"answer": answer_text, "sources": sources}

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
    print("\n--- Sources ---")
    for s in result["sources"]:
        print(f"  [{s['marker']}] {s['ticker']} {s['filing_type']} ({s['filing_date']}) — {s['section']}")
        print(f"      {s['source_url']}")
