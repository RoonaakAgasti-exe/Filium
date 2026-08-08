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
    return not (lowered.startswith("your_") or lowered.startswith("your ")
                or lowered.startswith("replace_with_") or lowered.startswith("replace_with ")
                or lowered in ("changeme", "change_this_in_production",
                               "generate_a_long_random_string_here"))

DATABASE_URL = _get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
JWT_SECRET_KEY = _get("JWT_SECRET_KEY")
ENVIRONMENT = _get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT in ("production", "prod", "staging", "stage")

CORS_ORIGINS = [o.strip() for o in _get("CORS_ORIGINS", "*").split(",") if o.strip()]

LLM_BASE_URL = _get("LLM_BASE_URL")
LLM_API_KEY = _get("LLM_API_KEY") or _get("OPENAI_API_KEY")
LLM_MODEL = _get("LLM_MODEL", "gpt-4o-mini")
LLM_ENABLED = _looks_real(LLM_API_KEY)

OPENAI_API_KEY = LLM_API_KEY
OPENAI_ENABLED = LLM_ENABLED

EMBEDDING_MODEL = _get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BASE_URL = _get("EMBEDDING_BASE_URL")
EMBEDDING_API_KEY = _get("EMBEDDING_API_KEY") or (LLM_API_KEY if not LLM_BASE_URL else "")

REMOTE_EMBEDDINGS_AVAILABLE = _looks_real(EMBEDDING_API_KEY) and (
    bool(EMBEDDING_BASE_URL) or not LLM_BASE_URL
)

EMBEDDING_PROVIDER = _get("EMBEDDING_PROVIDER", "auto").lower()
LOCAL_EMBEDDING_MODEL = _get("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

ALPACA_API_KEY = _get("ALPACA_API_KEY")
ALPACA_SECRET_KEY = _get("ALPACA_SECRET_KEY")
ALPACA_BASE_URL = _get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = _get("ALPACA_DATA_URL", "https://data.alpaca.markets")
ALPACA_ENABLED = _looks_real(ALPACA_API_KEY) and _looks_real(ALPACA_SECRET_KEY)

ALPACA_DATA_FEED = _get("ALPACA_DATA_FEED", "iex")

FMP_API_KEY = _get("FMP_API_KEY")
FMP_ENABLED = _looks_real(FMP_API_KEY)

LIVE_QUOTES_ENABLED = _get_bool("LIVE_QUOTES", True)

QUOTE_CACHE_TTL_SECONDS = int(_get("QUOTE_CACHE_TTL_SECONDS", "60") or 60)

NEWS_API_KEY = _get("NEWS_API_KEY")
FINNHUB_API_KEY = _get("FINNHUB_API_KEY")
NEWS_ENABLED = _looks_real(NEWS_API_KEY) or _looks_real(FINNHUB_API_KEY)

SMTP_HOST = _get("SMTP_HOST")
SMTP_PORT = int(_get("SMTP_PORT", "587") or 587)
SMTP_USER = _get("SMTP_USER")
SMTP_PASSWORD = _get("SMTP_PASSWORD")
SMTP_FROM = _get("SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = _get_bool("SMTP_USE_TLS", True)
EMAIL_ENABLED = bool(
    SMTP_HOST and _looks_real(SMTP_HOST)
    and SMTP_USER and _looks_real(SMTP_USER)
    and SMTP_FROM and _looks_real(SMTP_FROM)
)

STARTING_CASH = float(_get("STARTING_CASH", "100000") or 100000)

GUEST_EMAIL_DOMAINS = ("paper.fincopilot.app", "paper.fincopilot.local")

def is_guest_email(address: str | None) -> bool:
    """True for the synthetic addresses minted for browser-local paper accounts."""
    if not address:
        return False
    return address.lower().endswith(tuple(f"@{d}" for d in GUEST_EMAIL_DOMAINS))

EDGAR_USER_AGENT = _get("EDGAR_USER_AGENT", "FinCopilot dev dev@example.com")

def integration_status() -> dict:
    """Surfaced at /health so a deployment can be checked without guessing."""
    import embeddings

    return {
        "openai": OPENAI_ENABLED,
        "llm": {
            "configured": LLM_ENABLED,
            "model": LLM_MODEL,
            "endpoint": "default (OpenAI)" if not LLM_BASE_URL else LLM_BASE_URL,
        },
        "alpaca": ALPACA_ENABLED,
        "news": NEWS_ENABLED,
        "email": EMAIL_ENABLED,
        "live_quotes": LIVE_QUOTES_ENABLED,
        "fmp": FMP_ENABLED,
        "embeddings": embeddings.active_provider(),
    }
