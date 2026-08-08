# companies.py — API router for company data.
pass

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extensions import connection as PgConnection

from db import get_conn

router = APIRouter(prefix="/companies", tags=["companies"])

@router.get("/{ticker}")
def get_company(ticker: str, conn: PgConnection = Depends(get_conn)):
    pass
    ticker = ticker.upper()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ticker, name, sector, cik FROM companies WHERE ticker = %s",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No company record for {ticker}. "
                       f"Trading will create a stub row; ingest filings to enable chat.",
            )

        cur.execute(
            "SELECT filing_type, filing_date FROM filings WHERE ticker = %s "
            "ORDER BY filing_date DESC LIMIT 10",
            (ticker,),
        )
        filings = [{"filing_type": r[0], "filing_date": str(r[1])} for r in cur.fetchall()]

        cur.execute(
            "SELECT COUNT(*) FROM price_history WHERE ticker = %s",
            (ticker,),
        )
        price_rows = cur.fetchone()[0]
    finally:
        cur.close()

    return {
        "ticker": row[0],
        "name": row[1],
        "sector": row[2],
        "cik": row[3],
        "filings": filings,
        "price_history_rows": price_rows,
    }