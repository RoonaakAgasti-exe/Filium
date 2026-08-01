"""
db.py

Shared database connection pool for the FastAPI app. Using a pool
instead of one connection per request avoids the overhead of a fresh
TCP + auth handshake on every API call.
"""

import logging
import os
import threading
from contextlib import contextmanager

from dotenv import load_dotenv
from psycopg2 import OperationalError, pool

load_dotenv()

logger = logging.getLogger("fincopilot.db")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

# ThreadedConnectionPool, not SimpleConnectionPool: FastAPI runs every
# `def` (non-async) endpoint in a worker threadpool, so several requests
# genuinely touch the pool at once. SimpleConnectionPool does no locking
# and will hand the same connection to two threads under load.
_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def init_pool() -> pool.ThreadedConnectionPool:
    """
    Creates the pool on first use rather than at import time. Importing
    this module must not require a reachable database — otherwise the
    whole app fails to import when Postgres is briefly down, and tests
    that never touch the DB can't run at all.
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is None:
            _pool = pool.ThreadedConnectionPool(POOL_MIN, POOL_MAX, DB_URL)
            logger.info("Database pool initialised (%s-%s connections)", POOL_MIN, POOL_MAX)
    return _pool


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


@contextmanager
def connection():
    """
    Context-managed connection for use outside FastAPI's dependency
    system (scripts, background jobs, startup checks).
    """
    p = init_pool()
    conn = p.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _return(p, conn)


def get_conn():
    """FastAPI dependency — yields a connection, always returns it to the pool."""
    p = init_pool()
    try:
        conn = p.getconn()
    except OperationalError as exc:  # pragma: no cover - depends on live DB
        logger.error("Could not check out a database connection: %s", exc)
        raise

    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _return(p, conn)


def _return(p: pool.ThreadedConnectionPool, conn) -> None:
    """
    Roll back before handing the connection back. Without this, a request
    that raised mid-transaction leaves the connection in
    InFailedSqlTransaction — and because it goes straight back into the
    pool, the *next* unrelated request to draw it gets
    "current transaction is aborted, commands ignored until end of
    transaction block" for no visible reason. Endpoints commit explicitly,
    so an unconditional rollback here only ever discards work that was
    never meant to land.
    """
    try:
        conn.rollback()
    except Exception:  # pragma: no cover - connection already dead
        logger.warning("Rollback failed while returning connection; discarding it")
        try:
            p.putconn(conn, close=True)
        except Exception:
            pass
        return

    try:
        p.putconn(conn)
    except Exception:  # pragma: no cover
        logger.warning("Failed to return connection to the pool")


# ---------------------------------------------------------------------
# Small query helpers
# ---------------------------------------------------------------------


def fetch_all(conn, sql: str, params: tuple = ()) -> list[dict]:
    """Runs a query and returns rows as dicts keyed by column name."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def fetch_one(conn, sql: str, params: tuple = ()) -> dict | None:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def to_vector_literal(embedding) -> str:
    """
    pgvector accepts '[1,2,3]'; psycopg2 renders a Python list as the
    Postgres *array* literal '{1,2,3}', which `::vector` then refuses with
    "operator does not exist: vector <=> numeric[]". Every embedding
    bound into SQL has to go through here first.
    """
    values = getattr(embedding, "tolist", None)
    if callable(values):
        embedding = embedding.tolist()
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"
