"""
routers/query.py — RAG endpoints.

  POST /query            single-company (or corpus-wide) question
  POST /query/peer       one question across several tickers, side by side
  POST /query/compare    year-over-year diff of one company's filings
  GET  /query/filings/{ticker}   what's been ingested, for the compare picker
  GET  /query/history    the signed-in user's past questions
"""

import logging

from fastapi import APIRouter, Depends
from psycopg2.extensions import connection as PgConnection

import rag
from auth import get_current_user_id
from db import get_conn
from models import CompareFilingsRequest, PeerQueryRequest, QueryRequest, QueryResponse

logger = logging.getLogger("fincopilot.query")

router = APIRouter(prefix="/query", tags=["query"])

def _log_query(conn: PgConnection, user_id: int, question: str, result: dict) -> None:
    """
    Records the exchange. Deliberately non-fatal: the user already has
    their answer, and losing a history row is not worth turning a
    successful response into a 500.
    """
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO query_history (user_id, query_text, response_text, cited_chunk_ids)
            VALUES (%s, %s, %s, %s)
            """,
            (user_id, question, result["answer"], [s["chunk_id"] for s in result["sources"]]),
        )
        conn.commit()
        cur.close()
    except Exception:
        logger.exception("Failed to record query history for user %s", user_id)
        conn.rollback()

@router.post("", response_model=QueryResponse)
def ask_question(payload: QueryRequest, user_id: int = Depends(get_current_user_id),
                 conn: PgConnection = Depends(get_conn)):
    result = rag.answer_query(conn, payload.question, payload.ticker)
    _log_query(conn, user_id, payload.question, result)
    return result

@router.post("/peer")
def ask_peer_question(payload: PeerQueryRequest, user_id: int = Depends(get_current_user_id),
                      conn: PgConnection = Depends(get_conn)):
    """
    Multi-company question. `tickers_missing` in the response names any
    requested company with nothing ingested, so a partial answer is never
    mistaken for a complete one.
    """
    result = rag.answer_peer_query(conn, payload.question, payload.tickers)
    _log_query(conn, user_id, payload.question, result)
    return result

@router.post("/compare")
def compare_filings(payload: CompareFilingsRequest, user_id: int = Depends(get_current_user_id),
                    conn: PgConnection = Depends(get_conn)):
    """
    Year-over-year filing comparison, e.g. "How did the risk factors
    change?". Defaults to the two most recent filings for the ticker.
    """
    result = rag.compare_filings(
        conn,
        payload.ticker,
        payload.question,
        earlier_filing_id=payload.earlier_filing_id,
        later_filing_id=payload.later_filing_id,
    )
    _log_query(conn, user_id, payload.question, result)
    return result

@router.get("/filings/{ticker}")
def list_filings(ticker: str, conn: PgConnection = Depends(get_conn)):
    """Ingested filings for a ticker — powers the comparison date pickers."""
    return {"ticker": ticker.upper(), "filings": rag.list_filings(conn, ticker)}

@router.get("/history")
def query_history(limit: int = 20, user_id: int = Depends(get_current_user_id),
                  conn: PgConnection = Depends(get_conn)):
    limit = max(1, min(limit, 100))
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, query_text, response_text, cited_chunk_ids, timestamp "
            "FROM query_history WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s",
            (user_id, limit),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "id": r[0],
            "question": r[1],
            "answer": r[2],
            "cited_chunk_ids": r[3] or [],
            "timestamp": r[4].isoformat(),
        }
        for r in rows
    ]
