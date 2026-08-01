"""
config.py

One place that reads the environment, so every module agrees on what is
configured and what isn't. The `*_ENABLED` flags exist because this app
is built to run with any subset of its optional integrations present:
missing an OpenAI key should disable the chat feature with a clear
message, not crash the process or 500 an unrelated endpoint.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _looks_real(value: str) -> bool:
    """
    Treats the placeholder values shipped in .env.example as "not set".
    Copying .env.example to .env and forgetting to edit it is the single
    most common way this app gets misconfigured, and a placeholder key
    produces a confusing 401 from the provider rather than an obvious
    "you haven't configured this yet".
    """
    if not value:
        return False
    lowered = value.lower()
    return not (lowered.startswith("your_") or lowered.startswith("your ") or
                lowered in ("changeme", "change_this_in_production",
                            "generate_a_long_random_string_here"))


# --- Core ------------------------------------------------------------

DATABASE_URL = _get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
JWT_SECRET_KEY = _get("JWT_SECRET_KEY", "dev-only-change-this-in-production")
ENVIRONMENT = _get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod")

# Comma-separated list, or "*" to allow any origin.
CORS_ORIGINS = [o.strip() for o in _get("CORS_ORIGINS", "*").split(",") if o.strip()]

# --- OpenAI ----------------------------------------------------------

OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_ENABLED = _looks_real(OPENAI_API_KEY)
EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "text-embedding-3-small")
LLM_MODEL = _get("LLM_MODEL", "gpt-4o-mini")

# --- Alpaca ----------------------------------------------------------

ALPACA_API_KEY = _get("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _get("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = _get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = _get("ALPACA_DATA_URL", "https://data.alpaca.markets")
ALPACA_ENABLED = _looks_real(ALPACA_API_KEY) and _looks_real(ALPACA_SECRET_KEY)

# --- News ------------------------------------------------------------

NEWS_API_KEY = _get("NEWS_API_KEY")
FINNHUB_API_KEY = _get("FINNHUB_API_KEY")
NEWS_ENABLED = _looks_real(NEWS_API_KEY) or _looks_real(FINNHUB_API_KEY)

# --- Email (optional alert delivery) ---------------------------------

SMTP_HOST = _get("SMTP_HOST")
SMTP_PORT = int(_get("SMTP_PORT", "587") or 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
SMTP_FROM = _get("SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = _get_bool("SMTP_USE_TLS", True)
EMAIL_ENABLED = bool(SMTP_HOST and _looks_real(SMTP_HOST))

# --- Trading ---------------------------------------------------------

STARTING_CASH = float(_get("STARTING_CASH", "100000") or 100000)

# --- SEC -------------------------------------------------------------

EDGAR_USER_AGENT = _get("EDGAR_USER_AGENT", "FinCopilot dev dev@example.com")


def integration_status() -> dict:
    """Surfaced at /health so a deployment can be checked without guessing."""
    return {
        "openai": OPENAI_ENABLED,
        "alpaca": ALPACA_ENABLED,
        "news": NEWS_ENABLED,
        "email": EMAIL_ENABLED,
    }
