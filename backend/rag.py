"""
rag.py

Retrieval-augmented answering over ingested SEC filings and earnings call
transcripts. This is the canonical implementation — `ingestion/answer_query.py`
is a thin CLI wrapper around it, and the eval harness imports from here too,
so the pipeline being measured is exactly the pipeline being served.

Three query shapes are supported:
  * single/no ticker  — `answer_query`
  * several tickers   — `answer_peer_query`, one side-by-side answer
  * one ticker, two filing dates — `compare_filings`, a year-over-year diff

All three retrieve with an exact cosine scan (see the note in schema.sql
about why the ivfflat index is deliberately deferred), optionally rerank
with a cross-encoder, and force the model to cite numbered sources that
are mapped back to real chunk IDs afterwards.
"""

import logging
import re

import embeddings
import llm
from db import to_vector_literal

logger = logging.getLogger("fincopilot.rag")

TOP_K_RETRIEVE = 8
TOP_K_FINAL = 4
EXCERPT_CHARS = 1200

SYSTEM_PROMPT = """You are a financial research assistant. You will be given
excerpts from a company's SEC filings or earnings calls, each labeled with a
source number like [1], [2].

Rules:
- Answer ONLY using information present in the excerpts below.
- If the excerpts don't contain the answer, say so plainly — do not guess or use outside knowledge.
- Every claim you make must end with the source number(s) it came from, like this: "...impacted margins [2]."
- Keep the answer concise and directly responsive to the question.
"""

PEER_SYSTEM_PROMPT = """You are a financial research assistant comparing several
companies. You will be given excerpts from multiple companies' filings, each
labeled with a source number like [1], [2] and tagged with its ticker.

Rules:
- Answer ONLY using information present in the excerpts below.
- Organise the answer company by company, so the comparison is easy to read.
- If a company's excerpts don't address the question, say so for that company
  explicitly rather than inferring from the others.
- Every claim must end with the source number(s) it came from, like "...higher costs [2]."
"""

COMPARE_SYSTEM_PROMPT = """You are a financial research assistant comparing the SAME
company's disclosures across two different filing periods.

You will be given two sets of excerpts: EARLIER and LATER, each labeled with a
source number like [1], [2].

Rules:
- Describe what CHANGED between the two periods: what was added, what was
  removed, what was reworded in a way that changes its meaning.
- Explicitly call out anything material that appears in one period but not the other.
- If the two periods say substantively the same thing, say so plainly — a
  non-change is a legitimate and useful finding.
- Every claim must end with the source number(s) it came from.
- Do not speculate about causes that the excerpts don't support.
"""

class NoDataError(RuntimeError):
    """Nothing has been ingested that could answer this."""

def retrieve_chunks(conn, query_embedding, tickers: list[str] | None = None,
                    top_k: int = TOP_K_RETRIEVE, filing_id: int | None = None,
                    filing_types: list[str] | None = None) -> list[dict]:
    """
    Exact cosine-similarity search over filing_chunks, optionally scoped by
    ticker(s), filing, or filing type.

    The embedding is bound as a pgvector literal string, not a Python list:
    psycopg2 renders a list as a Postgres array, and `'{...}'::vector` fails
    with "operator does not exist: vector <=> numeric[]".
    """
    vector_literal = to_vector_literal(query_embedding)

    where = []
    params: list = [vector_literal]

    if tickers:
        where.append("f.ticker = ANY(%s)")
        params.append([t.upper() for t in tickers])
    if filing_id is not None:
        where.append("f.id = %s")
        params.append(filing_id)
    if filing_types:
        where.append("f.filing_type = ANY(%s)")
        params.append(filing_types)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(top_k)

    sql = f"""
        SELECT fc.id, fc.chunk_text, fc.section_label, f.ticker, f.filing_type,
               f.filing_date, f.source_url, f.id AS filing_id,
               fc.embedding <=> %s::vector AS distance
        FROM filing_chunks fc
        JOIN filings f ON f.id = fc.filing_id
        {where_clause}
        ORDER BY distance
        LIMIT %s
    """

    cur = conn.cursor()
    try:
        cur.execute(sql, tuple(params))
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()

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
    if len(chunks) <= 1:
        return chunks[:top_k]

    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        logger.debug("Rerank skipped: sentence-transformers not installed")
        return chunks[:top_k]

    try:
        model = _get_reranker(CrossEncoder)
        pairs = [(query, c["chunk_text"]) for c in chunks]
        scores = model.predict(pairs)
    except Exception as exc:
        logger.warning("Reranker failed, falling back to embedding order: %s", exc)
        return chunks[:top_k]

    scored = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
    return [c for c, _ in scored[:top_k]]

_reranker = None

def _get_reranker(cross_encoder_cls):
    """Loads the cross-encoder once — it's ~90MB and slow to construct per request."""
    global _reranker
    if _reranker is None:
        _reranker = cross_encoder_cls("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker

def extract_citation_ids(answer_text: str) -> set[int]:
    """Pulls out every [N] marker the LLM used, e.g. '[1]' and '[2, 3]'."""
    matches = re.findall(r"\[(\d+(?:\s*,\s*\d+)*)\]", answer_text or "")
    ids = set()
    for m in matches:
        for part in m.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
    return ids

def build_source(marker: int, chunk: dict) -> dict:
    """
    Shapes one cited chunk for the API.

    `excerpt` is the actual retrieved text, which is what makes
    source-highlighted answers possible — the UI shows the passage the
    claim came from instead of just a link the reader has to go verify
    themselves in a 200-page PDF.
    """
    text = chunk["chunk_text"] or ""
    truncated = len(text) > EXCERPT_CHARS
    return {
        "marker": marker,
        "ticker": chunk["ticker"],
        "filing_type": chunk["filing_type"],
        "filing_date": str(chunk["filing_date"]),
        "section": chunk.get("section_label") or "Unlabelled section",
        "source_url": chunk["source_url"],
        "chunk_id": chunk["chunk_id"] if "chunk_id" in chunk else chunk["id"],
        "excerpt": text[:EXCERPT_CHARS] + ("…" if truncated else ""),
        "excerpt_truncated": truncated,
        "distance": float(chunk["distance"]) if chunk.get("distance") is not None else None,
    }

def build_prompt(query: str, chunks: list[dict], label: str = "Excerpts") -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        blocks.append(
            f"[{i}] (from {c['ticker']} {c['filing_type']}, {c['filing_date']}, "
            f"section: {c.get('section_label') or 'unlabelled'})\n{c['chunk_text']}"
        )
    return f"{label}:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {query}"

def _normalise(chunks: list[dict]) -> list[dict]:
    """retrieve_chunks returns `id`; the rest of this module expects `chunk_id`."""
    for c in chunks:
        c.setdefault("chunk_id", c.get("id"))
    return chunks

EXTRACTIVE_PREAMBLE = (
    "No answer-writing model is configured, so this is the raw evidence "
    "rather than a written answer — the passages below are the ones that "
    "matched your question most closely, in rank order."
)

def extractive_answer(chunks: list[dict]) -> str:
    """
    The no-LLM answer: quote what retrieval found and say that's what it is.

    Retrieval and generation fail independently — the local embedding
    model needs no key, so a keyless deployment can still find the right
    passage in a 200-page 10-K even though it cannot write prose about it.
    Returning the passages is most of the value of the feature and none of
    the risk: there is no model in the loop to invent a number, and the
    citation markers are the same [N] the UI already knows how to resolve
    to a highlighted source.
    """
    lines = [EXTRACTIVE_PREAMBLE, ""]
    for i, c in enumerate(chunks, start=1):
        section = c.get("section_label") or "unlabelled section"
        snippet = " ".join((c["chunk_text"] or "").split())[:400]
        lines.append(
            f"- {c['ticker']} {c['filing_type']} ({c['filing_date']}), {section}: "
            f"“{snippet}…” [{i}]"
        )
    return "\n".join(lines)

def generate_answer(prompt: str, system: str, chunks: list[dict]) -> tuple[str, bool]:
    """
    Returns (answer_text, was_generated).

    Every caller needs the same two-step: ask the LLM, and if there isn't
    one, degrade to evidence rather than to an error. `was_generated`
    travels back to the UI so it can label an extractive answer honestly
    instead of presenting a list of quotes as if a model had written it.
    """
    try:
        return llm.complete(prompt, system=system), True
    except llm.LLMUnavailable as exc:
        logger.info("Answering extractively: %s", exc)
        return extractive_answer(chunks), False

def answer_query(conn, query: str, ticker: str | None = None) -> dict:
    """Single-company (or corpus-wide) question answering."""
    tickers = [ticker] if ticker else None
    return _answer(conn, query, tickers, SYSTEM_PROMPT)

def answer_peer_query(conn, query: str, tickers: list[str]) -> dict:
    """
    Multi-company question answering (PDF: "Multi-company / peer queries").

    Retrieval runs per ticker rather than as one pooled top-k. A pooled
    search lets the most verbose company dominate the context window and
    crowd the others out entirely, which produces an answer that quietly
    covers two of the four companies asked about.
    """
    tickers = [t.upper() for t in tickers if t and t.strip()]
    if not tickers:
        raise ValueError("At least one ticker is required for a peer query.")

    embeddings.assert_matches_corpus(conn)
    embedding = llm.embed_query(query)

    per_ticker = max(2, TOP_K_FINAL // max(1, len(tickers)) + 1)
    collected: list[dict] = []
    covered: list[str] = []

    for t in tickers:
        candidates = _normalise(retrieve_chunks(conn, embedding, [t], TOP_K_RETRIEVE))
        if not candidates:
            continue
        covered.append(t)
        collected.extend(rerank_chunks(query, candidates, per_ticker))

    if not collected:
        raise NoDataError(
            "No filing data found for any of: " + ", ".join(tickers) +
            ". Ingest a filing for them first."
        )

    prompt = build_prompt(query, collected)
    answer_text, generated = generate_answer(prompt, PEER_SYSTEM_PROMPT, collected)
    cited = extract_citation_ids(answer_text)

    return {
        "answer": answer_text,
        "generated": generated,
        "sources": [build_source(i, c) for i, c in enumerate(collected, start=1) if i in cited],
        "all_sources": [build_source(i, c) for i, c in enumerate(collected, start=1)],
        "tickers_requested": tickers,
        "tickers_covered": covered,
        "tickers_missing": [t for t in tickers if t not in covered],
    }

def _answer(conn, query: str, tickers: list[str] | None, system_prompt: str) -> dict:
    embeddings.assert_matches_corpus(conn)
    embedding = llm.embed_query(query)
    candidates = _normalise(retrieve_chunks(conn, embedding, tickers, TOP_K_RETRIEVE))

    if not candidates:
        scope = f" for {', '.join(tickers)}" if tickers else ""
        raise NoDataError(
            f"No filing data found{scope}. Ingest a filing first: "
            f"python ingestion/ingest_filing.py <TICKER>"
        )

    top_chunks = rerank_chunks(query, candidates, TOP_K_FINAL)
    prompt = build_prompt(query, top_chunks)
    answer_text, generated = generate_answer(prompt, system_prompt, top_chunks)
    cited = extract_citation_ids(answer_text)

    return {
        "answer": answer_text,
        "generated": generated,
        "sources": [build_source(i, c) for i, c in enumerate(top_chunks, start=1) if i in cited],
        "all_sources": [build_source(i, c) for i, c in enumerate(top_chunks, start=1)],
    }

def list_filings(conn, ticker: str) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT f.id, f.ticker, f.filing_type, f.filing_date, f.source_url,
                   count(fc.id) AS chunk_count
            FROM filings f
            LEFT JOIN filing_chunks fc ON fc.filing_id = f.id
            WHERE f.ticker = %s
            GROUP BY f.id
            ORDER BY f.filing_date DESC
            """,
            (ticker.upper(),),
        )
        columns = [d[0] for d in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()

    for r in rows:
        r["filing_date"] = str(r["filing_date"])
    return rows

def compare_filings(conn, ticker: str, question: str,
                    earlier_filing_id: int | None = None,
                    later_filing_id: int | None = None) -> dict:
    """
    Answers "how did X change year over year?" by retrieving the passages
    most relevant to `question` from two separate filings and asking the
    model to diff them.

    Retrieval is scoped to one filing at a time. Searching both at once
    and hoping for balanced coverage doesn't work — the two filings are
    near-duplicates of each other, so a pooled top-k routinely returns
    the same passage twice from whichever filing happens to score
    marginally higher, and there is nothing to compare.
    """
    ticker = ticker.upper()
    filings = list_filings(conn, ticker)

    if len(filings) < 2 and (earlier_filing_id is None or later_filing_id is None):
        raise NoDataError(
            f"Need at least two ingested filings for {ticker} to compare "
            f"(found {len(filings)}). Ingest another period first, e.g. "
            f"python ingestion/fetch_filing.py {ticker} --count 2"
        )

    by_id = {f["id"]: f for f in filings}

    if later_filing_id is None:
        later = filings[0]
    else:
        later = by_id.get(later_filing_id)
    if earlier_filing_id is None:
        earlier = filings[1]
    else:
        earlier = by_id.get(earlier_filing_id)

    if earlier is None or later is None:
        raise NoDataError(f"One of the requested filings doesn't belong to {ticker}.")
    if earlier["id"] == later["id"]:
        raise ValueError("Pick two different filings to compare.")

    if earlier["filing_date"] > later["filing_date"]:
        earlier, later = later, earlier

    embeddings.assert_matches_corpus(conn)
    embedding = llm.embed_query(question)

    earlier_chunks = rerank_chunks(
        question,
        _normalise(retrieve_chunks(conn, embedding, [ticker], TOP_K_RETRIEVE, filing_id=earlier["id"])),
        TOP_K_FINAL,
    )
    later_chunks = rerank_chunks(
        question,
        _normalise(retrieve_chunks(conn, embedding, [ticker], TOP_K_RETRIEVE, filing_id=later["id"])),
        TOP_K_FINAL,
    )

    if not earlier_chunks or not later_chunks:
        raise NoDataError(
            "One of the two filings has no indexed content matching that question."
        )

    combined = earlier_chunks + later_chunks
    offset = len(earlier_chunks)

    earlier_block = "\n\n".join(
        f"[{i}] ({earlier['filing_type']} {earlier['filing_date']}, "
        f"section: {c.get('section_label') or 'unlabelled'})\n{c['chunk_text']}"
        for i, c in enumerate(earlier_chunks, start=1)
    )
    later_block = "\n\n".join(
        f"[{offset + i}] ({later['filing_type']} {later['filing_date']}, "
        f"section: {c.get('section_label') or 'unlabelled'})\n{c['chunk_text']}"
        for i, c in enumerate(later_chunks, start=1)
    )

    prompt = (
        f"EARLIER — {ticker} {earlier['filing_type']} filed {earlier['filing_date']}:\n\n"
        f"{earlier_block}\n\n"
        f"LATER — {ticker} {later['filing_type']} filed {later['filing_date']}:\n\n"
        f"{later_block}\n\n"
        f"Question: {question}"
    )

    answer_text, generated = generate_answer(prompt, COMPARE_SYSTEM_PROMPT, combined)
    cited = extract_citation_ids(answer_text)

    return {
        "ticker": ticker,
        "question": question,
        "answer": answer_text,
        "generated": generated,
        "earlier_filing": {
            "id": earlier["id"], "filing_type": earlier["filing_type"],
            "filing_date": earlier["filing_date"], "source_url": earlier["source_url"],
        },
        "later_filing": {
            "id": later["id"], "filing_type": later["filing_type"],
            "filing_date": later["filing_date"], "source_url": later["source_url"],
        },
        "sources": [build_source(i, c) for i, c in enumerate(combined, start=1) if i in cited],
        "all_sources": [build_source(i, c) for i, c in enumerate(combined, start=1)],
    }
