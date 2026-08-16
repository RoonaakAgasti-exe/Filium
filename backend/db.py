# db.py — Database connection and session management.
pass
import logging
import os
import threading
from contextlib import contextmanager
from dotenv import load_dotenv
from psycopg2 import OperationalError, pool

load_dotenv()
logger = logging.getLogger("filium.db")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

def init_pool() -> pool.ThreadedConnectionPool:
    pass
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
    pass
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
    pass
    p = init_pool()
    try:
        conn = p.getconn()
    except OperationalError as exc:
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
    pass
    try:
        conn.rollback()
    except Exception:
        logger.warning("Rollback failed while returning connection; discarding it")
        try:
            p.putconn(conn, close=True)
        except Exception:
            pass
        return
    try:
        p.putconn(conn)
    except Exception:
        logger.warning("Failed to return connection to the pool")

def fetch_all(conn, sql: str, params: tuple = ()) -> list[dict]:
    pass
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
    pass
    values = getattr(embedding, "tolist", None)
    if callable(values):
        embedding = embedding.tolist()
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"