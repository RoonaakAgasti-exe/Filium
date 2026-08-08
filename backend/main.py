"""main.py — FastAPI app entrypoint."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import config
import db
import embeddings
from alpaca_client import AlpacaUnavailable
from embeddings import CorpusMismatch
import llm
from llm import LLMUnavailable
from market_data import PriceUnavailable
from rag import NoDataError
from routers import (
    alerts,
    auth_router,
    companies,
    news,
    portfolio,
    predictions,
    prices,
    public,
    query,
    sandbox,
    trade,
    wallet_router,
    watchlist,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fincopilot")

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db.init_pool()
    except Exception as exc:
        logger.error("Database pool could not be initialised at startup: %s", exc)

    status = config.integration_status()

    logger.info("Embedding provider: %s", status.get("embeddings"))

    disabled = [name for name, on in status.items() if on is False]
    if disabled:
        logger.info("Optional integrations not configured: %s", ", ".join(disabled))

    yield

    db.close_pool()

app = FastAPI(
    title="FinCopilot API",
    version="2.0.0",
    description=(
        "AI research assistant for SEC filings, with a sentiment-augmented price "
        "predictor and paper trading. Paper trading only — not investment advice."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=("*" not in config.CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(NoDataError)
async def handle_no_data(request: Request, exc: NoDataError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})

@app.exception_handler(LLMUnavailable)
async def handle_llm_unavailable(request: Request, exc: LLMUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(CorpusMismatch)
async def handle_corpus_mismatch(request: Request, exc: CorpusMismatch):
    return JSONResponse(status_code=409, content={"detail": str(exc)})

@app.exception_handler(PriceUnavailable)
async def handle_price_unavailable(request: Request, exc: PriceUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(AlpacaUnavailable)
async def handle_alpaca_unavailable(request: Request, exc: AlpacaUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})

@app.exception_handler(ValueError)
async def handle_value_error(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})

app.include_router(auth_router.router)
app.include_router(companies.router)
app.include_router(wallet_router.router)
app.include_router(query.router)
app.include_router(predictions.router)
app.include_router(prices.router)
app.include_router(news.router)
app.include_router(watchlist.router)
app.include_router(alerts.router)
app.include_router(trade.router)
app.include_router(portfolio.router)
app.include_router(sandbox.router)
app.include_router(public.router)

@app.get("/health", tags=["meta"])
def health_check():
    """
    Reports database reachability and which optional integrations are
    configured. Deliberately returns 200 even when the database is down —
    a load balancer removing the container hides exactly the information
    you need to debug it.
    """
    database_ok = True
    database_error = None
    try:
        with db.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
    except Exception as exc:
        database_ok = False
        database_error = str(exc)

    try:
        embedding_status = embeddings.describe()
    except Exception as exc:
        embedding_status = {"provider": None, "model": None, "error": str(exc)}

    return {
        "status": "ok" if database_ok else "degraded",
        "database": {"connected": database_ok, "error": database_error},
        "integrations": config.integration_status(),
        "embeddings": embedding_status,
        "llm_degraded_reason": llm.degraded_reason(),
        "version": app.version,
    }
