"""routers/auth_router.py — /auth/register and /auth/login."""

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extensions import connection as PgConnection

from auth import create_access_token, create_user, get_current_user_id, get_user_by_email, verify_password
from db import get_conn
from models import TokenResponse, UserLogin, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, conn: PgConnection = Depends(get_conn)):
    existing = get_user_by_email(conn, payload.email)
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = create_user(conn, payload.email, payload.password)
    token = create_access_token(user["id"], user["email"])
    return TokenResponse(access_token=token)


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLogin, conn: PgConnection = Depends(get_conn)):
    user = get_user_by_email(conn, payload.email)
    if user is None or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    token = create_access_token(user["id"], user["email"])
    return TokenResponse(access_token=token)


@router.get("/me")
def me(user_id: int = Depends(get_current_user_id), conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT u.id, u.email, u.created_at, w.cash_balance "
            "FROM users u LEFT JOIN wallets w ON w.user_id = u.id WHERE u.id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {
        "id": row[0],
        "email": row[1],
        "created_at": row[2].isoformat(),
        "cash_balance": float(row[3]) if row[3] is not None else None,
    }
