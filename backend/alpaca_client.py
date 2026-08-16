# alpaca_client.py — Alpaca brokerage API client.
pass
import logging
import requests
import config

logger = logging.getLogger("filium.alpaca")
TIMEOUT = 10

class AlpacaUnavailable(RuntimeError):
    pass

def is_configured() -> bool:
    return config.ALPACA_ENABLED

def _headers() -> dict:
    if not config.ALPACA_ENABLED:
        raise AlpacaUnavailable("Alpaca API keys are not configured (set ALPACA_API_KEY and ALPACA_SECRET_KEY)")
    return {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY
    }

def _feed_error(ticker: str) -> AlpacaUnavailable:
    pass
    return AlpacaUnavailable(
        f"Alpaca rejected the market data request for {ticker} on feed "
        f"'{config.ALPACA_DATA_FEED}' (403). Free accounts only get the IEX "
        f"feed — set ALPACA_DATA_FEED=iex."
    )

def get_latest_price(ticker: str) -> float:
    pass
    headers = _headers()
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{ticker}/trades/latest"
    try:
        resp = requests.get(url, headers = headers, params = {"feed": config.ALPACA_DATA_FEED}, timeout = TIMEOUT)
        if resp.status_code == 403:
            raise _feed_error(ticker)
        resp.raise_for_status()
        data = resp.json()
        return float(data["trade"]["p"])
    except requests.RequestException as exc:
        raise AlpacaUnavailable(f"Alpaca request failed for {ticker}: {exc}") from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise AlpacaUnavailable(f"Unexpected Alpaca response shape for {ticker}: {exc}") from exc

def get_daily_bars(ticker: str, start: str, end: str, limit: int = 1000) -> list[dict]:
    pass
    headers = _headers()
    url = f"{config.ALPACA_DATA_URL}/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Day",
        "start": start,
        "end": end,
        "limit": limit,
        "adjustment": "split",
        "feed": config.ALPACA_DATA_FEED
    }
    try:
        resp = requests.get(url, headers = headers, params = params, timeout = 30)
        if resp.status_code == 403:
            raise _feed_error(ticker)
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
            "volume": bar.get("v")
        }
        for bar in bars]

def submit_paper_order(ticker: str, qty: float, side: str) -> dict:
    pass
    headers = _headers()
    url = f"{config.ALPACA_BASE_URL}/v2/orders"
    payload = {
        "symbol": ticker,
        "qty": str(qty),
        "side": side,
        "type": "market",
        "time_in_force": "day"
    }
    try:
        resp = requests.post(url, json = payload, headers = headers, timeout = TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise AlpacaUnavailable(f"Alpaca order submission failed for {ticker}: {exc}") from exc