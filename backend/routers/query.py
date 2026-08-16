# query.py — API router for RAG chat and document querying.
pass

import logging

from fastapi import APIRouter, Depends
from psycopg2.extensions import connection as PgConnection

import rag
from auth import get_current_user_id
from db import get_conn
from models import CompareFilingsRequest, PeerQueryRequest, QueryRequest, QueryResponse

logger = logging.getLogger("filium.query")

router = APIRouter(prefix="/query", tags=["query"])

def _log_query(conn: PgConnection, user_id: int, question: str, result: dict) -> None:
    pass

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
    pass

    result = rag.answer_peer_query(conn, payload.question, payload.tickers)
    _log_query(conn, user_id, payload.question, result)
    return result

@router.post("/compare")
def compare_filings(payload: CompareFilingsRequest, user_id: int = Depends(get_current_user_id),
                    conn: PgConnection = Depends(get_conn)):
    pass

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
    pass
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