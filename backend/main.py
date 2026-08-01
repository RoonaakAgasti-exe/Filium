"""main.py — FastAPI app entrypoint."""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Make sibling modules importable whether the app is launched as
# `uvicorn main:app` from inside backend/ or `uvicorn backend.main:app`
# from the repo root.
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402
from alpaca_client import AlpacaUnavailable  # noqa: E402
from llm import LLMUnavailable  # noqa: E402
from market_data import PriceUnavailable  # noqa: E402
from rag import NoDataError  # noqa: E402
from routers import (  # noqa: E402
    alerts,
    auth_router,
    news,
    portfolio,
    predictions,
    prices,
    public,
    query,
    sandbox,
    trade,
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
        # Don't refuse to boot — /health should come up and report the
        # problem, which is far easier to diagnose in a container than a
        # crash-looping process with one line of traceback.
        logger.error("Database pool could not be initialised at startup: %s", exc)

    disabled = [name for name, on in config.integration_status().items() if not on]
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

# Without this the app is completely non-functional in the default Docker
# setup: the frontend is served from http://localhost (port 80) and calls
# the API on http://localhost:8000, which the browser treats as a
# cross-origin request and blocks outright.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=("*" not in config.CORS_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Exception handlers ----------------------------------------------
# These turn the app's own "this isn't configured / no data yet" signals
# into honest status codes instead of 500s, so the UI can tell a user
# what to do about it.


@app.exception_handler(NoDataError)
async def handle_no_data(request: Request, exc: NoDataError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(LLMUnavailable)
async def handle_llm_unavailable(request: Request, exc: LLMUnavailable):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


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

    return {
        "status": "ok" if database_ok else "degraded",
        "database": {"connected": database_ok, "error": database_error},
        "integrations": config.integration_status(),
        "version": app.version,
    }
