"""
alpaca_client.py

Thin wrapper around Alpaca's Paper Trading REST API. Real market prices,
fake money, zero real financial risk — see project README for why paper
trading was chosen over a fully custom wallet simulation.

Every function raises `AlpacaUnavailable` (never a bare requests error)
when keys are missing or the API is unreachable, so callers can fall back
to stored price history instead of returning a 500. Building the auth
headers at call time rather than import time matters: a None-valued
header makes `requests` raise InvalidHeader from deep inside the stack,
which reads like a bug rather than "you haven't set your API keys".
"""

import logging

import requests

import config

logger = logging.getLogger("fincopilot.alpaca")

TIMEOUT = 10


class AlpacaUnavailable(RuntimeError):
    """Alpaca isn't configured, or the request to it failed."""


def is_configured() -> bool:
    return config.ALPACA_ENABLED


def _headers() -> dict:
    if not config.ALPACA_ENABLED:
        raise AlpacaUnavailable(
            "Alpaca API keys are not configured (set ALPACA_API_KEY and ALPACA_SECRET_KEY)"
        )
    return {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }


def get_latest_price(ticker: str) -> float:
    """Latest trade price for a ticker from Alpaca's market data API."""
    headers = _headers()
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{ticker}/trades/latest"

    try:
        resp = requests.get(url, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return float(data["trade"]["p"])
    except requests.RequestException as exc:
        raise AlpacaUnavailable(f"Alpaca request failed for {ticker}: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise AlpacaUnavailable(f"Unexpected Alpaca response shape for {ticker}: {exc}") from exc


def get_daily_bars(ticker: str, start: str, end: str, limit: int = 1000) -> list[dict]:
    """
    Daily OHLCV bars between two ISO dates. Used by the price ingestion
    job when Alpaca is configured; yfinance covers the no-key case.
    """
    headers = _headers()
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{ticker}/bars"
    params = {"timeframe": "1Day", "start": start, "end": end, "limit": limit, "adjustment": "split"}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise AlpacaUnavailable(f"Alpaca bars request failed for {ticker}: {exc}") from exc

    bars = payload.get("bars") or []
    return [
        {
            "date": bar["t"][:10],
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
        }
        for bar in bars
    ]


def submit_paper_order(ticker: str, qty: float, side: str) -> dict:
    """
    Submits a market order to Alpaca's paper trading account.
    side must be 'buy' or 'sell'. Returns Alpaca's order confirmation.

    Note: this places an actual (paper) order with Alpaca, which fills
    asynchronously. For a portfolio project, treating the submitted
    price as the fill price is a reasonable simplification — a
    production system would poll the order status or use a webhook to
    confirm the actual fill price before updating the ledger.

    The app's own ledger in `wallets`/`holdings` is the source of truth;
    this is only called when MIRROR_ORDERS_TO_ALPACA is enabled.
    """
    headers = _headers()
    url = f"{config.ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise AlpacaUnavailable(f"Alpaca order submission failed for {ticker}: {exc}") from exc
