pass
from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PgConnection
from db import get_conn

router = APIRouter(prefix = "/news", tags = ["news"])
@router.get("/{ticker}")
def get_news(ticker:str, limit:int = Query(default = 30, ge = 1, le = 200), conn:PgConnection = Depends(get_conn)):
    ticker = ticker.upper()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT published_date, headline, summary, url, source, sentiment_score "
            "FROM news_articles WHERE ticker = %s "
            "ORDER BY published_date DESC, id DESC LIMIT %s", ticker, limit)
        rows = cur.fetchall()
    finally:
        cur.close()
    return {
        "ticker": ticker,
        "articles": [{
                "published_date": str(r[0]),
                "headline": r[1],
                "summary": r[2],
                "url": r[3],
                "source": r[4],
                "sentiment_score": float(r[5]) if r[5] is not None else None} for r in rows]}

@router.get("/{ticker}/sentiment-timeline")
def sentiment_timeline(ticker:str, days:int = Query(default = 180, ge = 1, le = 1825), conn:PgConnection = Depends(get_conn)):
    pass
    ticker = ticker.upper()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT COALESCE(p.date, s.date) AS date,
                   p.close,
                   s.score,
                   s.article_count
            FROM (
                SELECT date, close FROM price_history
                WHERE ticker = %s ORDER BY date DESC LIMIT %s
            ) p
            FULL OUTER JOIN (
                SELECT date, score, article_count FROM sentiment_scores
                WHERE ticker = %s AND source = 'news' ORDER BY date DESC LIMIT %s
            ) s ON s.date = p.date
            ORDER BY 1
            """, ticker, days, ticker, days)
        rows = cur.fetchall()
    finally:
        cur.close()
    if not rows:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail = f"No price or sentiment data for {ticker}. Run ingestion/fetch_prices.py and ingestion/fetch_news.py first.")
    points = [
        {
            "date":str(r[0]),
            "close":float(r[1]) if r[1] is not None else None,
            "sentiment":float(r[2]) if r[2] is not None else None,
            "article_count":r[3] or 0
        }for r in rows]
    scored = [p["sentiment"] for p in points if p["sentiment"] is not None]
    return {
        "ticker":ticker,
        "points":points,
        "days_with_sentiment":len(scored),
        "average_sentiment":(sum(scored) / len(scored)) if scored else None}