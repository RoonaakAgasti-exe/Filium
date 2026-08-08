"""
auth.py

Password hashing (bcrypt) and JWT issuing/verification for
/auth/register and /auth/login, plus a FastAPI dependency
(get_current_user) that other routers use to require login.
"""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from psycopg2.extensions import connection as PgConnection

import config

logger = logging.getLogger("fincopilot.auth")

def _jwt_secret_configured(value: str) -> bool:
    """Treat .env.example placeholders the same as unset."""
    if not value:
        return False
    lowered = value.lower()
    return not (lowered.startswith("your_") or lowered.startswith("your ")
                or lowered in ("changeme", "change_this_in_production",
                                 "generate_a_long_random_string_here"))

SECRET_KEY = config.JWT_SECRET_KEY if _jwt_secret_configured(config.JWT_SECRET_KEY) else ""
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

DEFAULT_DEV_SECRET = "dev-only-change-this-in-production"

if config.IS_PRODUCTION and (not SECRET_KEY or SECRET_KEY == DEFAULT_DEV_SECRET):
    raise RuntimeError(
        "JWT_SECRET_KEY must be set to a real random value when ENVIRONMENT=production "
        "or ENVIRONMENT=staging"
    )
if not SECRET_KEY:
    SECRET_KEY = DEFAULT_DEV_SECRET

if SECRET_KEY == DEFAULT_DEV_SECRET:
    logger.warning("Using the default development JWT secret — set JWT_SECRET_KEY before deploying")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def hash_password(plain_password: str) -> str:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.hashpw(pw_bytes, bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))

def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    payload = decode_access_token(token)
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_user_by_email(conn: PgConnection, email: str):
    cur = conn.cursor()
    cur.execute("SELECT id, email, hashed_password FROM users WHERE email = %s", (email,))
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    return {"id": row[0], "email": row[1], "hashed_password": row[2]}

def create_user(conn: PgConnection, email: str, password: str) -> dict:
    """
    Creates the user and their starting wallet in one transaction — a user
    without a wallet row can't trade, and every trade path would have to
    special-case that state forever.
    """
    hashed = hash_password(password)
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (email, hashed_password) VALUES (%s, %s) RETURNING id",
            (email, hashed),
        )
        user_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO wallets (user_id, cash_balance) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO NOTHING",
            (user_id, config.STARTING_CASH),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {"id": user_id, "email": email}
