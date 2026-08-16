import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
import fetch_news
import fetch_prices
from backtest_daily import run_backtest
from predict_daily import run_daily_predictions
import company_info
import wallet

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
sys.path.append(str(Path(__file__).parent.parent / "backend"))
load_dotenv()
logging.basicConfig(level = logging.INFO, format = "%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("filium.scheduler")
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
SCHEDULER_HOUR = int(os.getenv("SCHEDULER_HOUR", "6") or 6)
RUN_SENTIMENT = (os.getenv("SCHEDULER_RUN_SENTIMENT", "1").strip().lower() not in ("0", "false", "no", "off"))
PRICE_DAYS = int(os.getenv("SCHEDULER_PRICE_DAYS", "7") or 7)
NEWS_DAYS = int(os.getenv("SCHEDULER_NEWS_DAYS", "3") or 3)

def _watchlisted(conn) -> list[str]:
    return fetch_prices.watchlisted_tickers(conn)

def ingest_prices(conn, tickers:list[str]) -> None:
    for ticker in tickers:
        try:
            result = fetch_prices.fetch_and_store(conn, ticker, PRICE_DAYS)
            logger.info("prices %s: %s row(s) via %s", ticker, result["rows"], result["source"])
        except Exception:
            logger.exception("Price ingestion failed for %s", ticker)

def ingest_news(conn, tickers:list[str]) -> None:
    for ticker in tickers:
        try:
            result = fetch_news.fetch_and_store(conn, ticker, NEWS_DAYS)
            logger.info("news %s: %s new of %s fetched via %s", ticker, result["inserted"], result["fetched"], result["source"])
        except Exception:
            logger.exception("News ingestion failed for %s", ticker)

def score_sentiment(conn, tickers: list[str]) -> None:
    from sentiment import FinBertScorer, run_sentiment_scoring
    try:
        scorer = FinBertScorer()
    except Exception:
        logger.exception("Could not load FinBERT — skipping sentiment scoring for this run")
        return
    for ticker in tickers:
        try:
            result = run_sentiment_scoring(conn, ticker, "news", scorer)
            logger.info("sentiment %s: scored %s text(s) across %s day(s)", ticker, result["scored"], result["days"])
        except Exception:
            logger.exception("Sentiment scoring failed for %s", ticker)

def enrich_companies(conn) -> None:
    try:
        result = company_info.backfill_all(conn)
    except Exception:
        logger.exception("Company enrichment failed")
        conn.rollback()
        return
    logger.info("companies: enriched %s of %s placeholder row(s)", result["updated"], result["examined"])

def snapshot_portfolios(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, close
            FROM price_history
            WHERE close IS NOT NULL
            ORDER BY ticker, date DESC
            """)
        prices = {row[0]:float(row[1]) for row in cur.fetchall()}
        cur.execute("SELECT user_id FROM wallets ORDER BY user_id")
        user_ids = [row[0] for row in cur.fetchall()]
    finally:
        cur.close()
    if not user_ids:
        logger.info("snapshots: no wallets yet — nothing to record")
        return
    today = date.today()
    saved = 0
    for user_id in user_ids:
        try:
            portfolio = wallet.get_portfolio(conn, user_id, prices)
            wallet.save_daily_snapshot(conn, user_id, today, portfolio)
            saved += 1
        except Exception:
            logger.exception("Snapshot failed for user %s", user_id)
            conn.rollback()
    logger.info("snapshots: recorded %s of %s portfolio(s) for %s", saved, len(user_ids), today)

def daily_job(skip_sentiment:bool = False) -> None:
    logger.info("=== Daily cycle starting ===")
    try:
        conn = psycopg2.connect(DB_URL)
    except Exception:
        logger.exception("Database unreachable — skipping this cycle entirely")
        return
    try:
        tickers = _watchlisted(conn)
        if tickers:
            logger.info("Watchlisted tickers: %s", ", ".join(tickers))
            ingest_prices(conn, tickers)
            ingest_news(conn, tickers)
            if RUN_SENTIMENT and not skip_sentiment:
                score_sentiment(conn, tickers)
            else:
                logger.info("Sentiment scoring disabled — using stored scores only")
        else:
            logger.info("No watchlisted tickers yet — nothing to ingest or predict. Add one from the app, or run the ingestion scripts manually.")
    finally:
        conn.close()
    if tickers:
        try:
            run_backtest()
        except Exception:
            logger.exception("Backtest step failed")
        try:
            run_daily_predictions()
        except Exception:
            logger.exception("Prediction step failed")
    try:
        conn = psycopg2.connect(DB_URL)
    except Exception:
        logger.exception("Database unreachable — skipping snapshots")
        return
    try:
        enrich_companies(conn)
        snapshot_portfolios(conn)
    finally:
        conn.close()
    logger.info("=== Daily cycle complete ===")

def main() -> None:
    parser = argparse.ArgumentParser(description = "Filium daily job runner")
    parser.add_argument("--now", action = "store_true", help = "Run the cycle once immediately and exit, instead of scheduling it")
    parser.add_argument("--skip-sentiment", action = "store_true", help = "Skip FinBERT scoring for this run")
    args = parser.parse_args()
    if args.now:
        daily_job(skip_sentiment = args.skip_sentiment)
        return
    scheduler = BlockingScheduler()
    scheduler.add_job(daily_job, "cron", hour = SCHEDULER_HOUR, minute = 0)
    logger.info("Scheduler started — daily cycle fires at %02d:00 server time. Run with --now to trigger one immediately.", SCHEDULER_HOUR)
    scheduler.start()

if __name__ == "__main__":
    main()