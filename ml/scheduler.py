"""
scheduler.py

Runs the whole daily cycle as one long-lived process, in dependency
order:

    1. fetch prices   — the new bar is what resolves yesterday's
                        prediction AND what today's features are built on
    2. fetch news     — raw headlines for the sentiment feature
    3. score sentiment — FinBERT over anything unscored
    4. backtest       — resolve every prediction whose outcome bar now exists
    5. predict        — log today's new prediction, outcome unknown
    6. snapshot       — value every portfolio at today's close
    7. alerts         — evaluate standing alert rules against all of the above

Steps 1-3 used to be missing: the job predicted and backtested against
whatever happened to be in the database, so on a deployment where nobody
ran the ingestion scripts by hand it would predict on stale features
forever and never resolve a single prediction. Ingestion has to be part
of the cycle, not a manual prerequisite.

Steps 6-7 were missing for the same reason. Nothing wrote
`portfolio_snapshots`, so Sharpe, volatility, drawdown and the benchmark
comparison were permanently stuck on "not enough history yet"; and alerts
only ran when someone pressed "check now" in the app, so a standing alert
on a deployment nobody had open never notified anyone.

Each step is isolated — a news API being down shouldn't stop predictions
from being logged, and a prediction failure shouldn't stop the next day's
run. Every step reports what it did, because "the scheduler is running"
and "the scheduler is doing anything useful" are different claims and the
logs are the only place to tell them apart.

For a portfolio project this is simpler than standing up a task queue —
save that complexity for if this ever needs to scale past one deployment.

Usage:
    python scheduler.py              # run forever, fire daily
    python scheduler.py --now        # run the cycle once and exit
    python scheduler.py --now --skip-sentiment
"""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

# The ingestion fetchers live in a sibling directory. Adding it here means
# `python ml/scheduler.py` works from a clone with no PYTHONPATH set —
# the Docker image sets it too, but the local path is the one people
# actually hit first.
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))

# Snapshots and alerts reuse the backend's wallet valuation and rule
# evaluation instead of reimplementing them here — two definitions of when
# an alert fires is exactly how the app and the nightly job start
# disagreeing. Appended rather than prepended so ml/ and ingestion/ keep
# priority on any name collision.
sys.path.append(str(Path(__file__).parent.parent / "backend"))

import psycopg2  # noqa: E402
from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

import fetch_news  # noqa: E402
import fetch_prices  # noqa: E402
from backtest_daily import run_backtest  # noqa: E402
from predict_daily import run_daily_predictions  # noqa: E402

import alerts_engine  # noqa: E402
import wallet  # noqa: E402

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fincopilot.scheduler")

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")

SCHEDULER_HOUR = int(os.getenv("SCHEDULER_HOUR", "6") or 6)

# Sentiment scoring pulls FinBERT (~400MB) on first run. Deployments that
# don't want that in the container can turn the step off and still get
# predictions from whatever sentiment is already stored.
RUN_SENTIMENT = (os.getenv("SCHEDULER_RUN_SENTIMENT", "1").strip().lower()
                 not in ("0", "false", "no", "off"))

# A short window each day, not the full history — enough to cover a long
# weekend plus a provider publishing late, without re-pulling two years
# of bars every morning against a rate-limited free tier.
PRICE_DAYS = int(os.getenv("SCHEDULER_PRICE_DAYS", "7") or 7)
NEWS_DAYS = int(os.getenv("SCHEDULER_NEWS_DAYS", "3") or 3)


def _watchlisted(conn) -> list[str]:
    return fetch_prices.watchlisted_tickers(conn)


def ingest_prices(conn, tickers: list[str]) -> None:
    for ticker in tickers:
        try:
            result = fetch_prices.fetch_and_store(conn, ticker, PRICE_DAYS)
            logger.info("prices %s: %s row(s) via %s", ticker, result["rows"], result["source"])
        except Exception:
            logger.exception("Price ingestion failed for %s", ticker)


def ingest_news(conn, tickers: list[str]) -> None:
    for ticker in tickers:
        try:
            result = fetch_news.fetch_and_store(conn, ticker, NEWS_DAYS)
            logger.info("news %s: %s new of %s fetched via %s",
                        ticker, result["inserted"], result["fetched"], result["source"])
        except Exception:
            logger.exception("News ingestion failed for %s", ticker)


def score_sentiment(conn, tickers: list[str]) -> None:
    # Imported lazily so the transformers/torch model load only happens
    # when this step actually runs — with the step disabled, the process
    # starts in seconds and never touches the Hugging Face hub.
    from sentiment import FinBertScorer, run_sentiment_scoring

    try:
        scorer = FinBertScorer()
    except Exception:
        logger.exception("Could not load FinBERT — skipping sentiment scoring for this run")
        return

    for ticker in tickers:
        try:
            result = run_sentiment_scoring(conn, ticker, "news", scorer)
            logger.info("sentiment %s: scored %s text(s) across %s day(s)",
                        ticker, result["scored"], result["days"])
        except Exception:
            logger.exception("Sentiment scoring failed for %s", ticker)


def snapshot_portfolios(conn) -> None:
    """
    Records one portfolio_snapshots row per user for today.

    Holdings are valued at the most recent stored close rather than a live
    quote: price ingestion has already run this cycle, so it's the same
    data every other step used, and it keeps this step off the network.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT ON (ticker) ticker, close
            FROM price_history
            WHERE close IS NOT NULL
            ORDER BY ticker, date DESC
            """
        )
        prices = {row[0]: float(row[1]) for row in cur.fetchall()}

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
            # A holding whose ticker has no stored price contributes no
            # market value. The snapshot still records cash plus everything
            # that could be priced, rather than dropping the user entirely
            # and leaving a hole in their equity curve.
            portfolio = wallet.get_portfolio(conn, user_id, prices)
            wallet.save_daily_snapshot(conn, user_id, today, portfolio)
            saved += 1
        except Exception:
            logger.exception("Snapshot failed for user %s", user_id)
            conn.rollback()

    logger.info("snapshots: recorded %s of %s portfolio(s) for %s",
                saved, len(user_ids), today)


def check_alerts(conn) -> None:
    """
    Evaluates every active alert rule against the data this cycle wrote.

    `evaluate_all_alerts` suppresses a rule that already fired today, so
    running this nightly alongside the app's "check now" button can't
    double-notify anyone.
    """
    try:
        fired = alerts_engine.evaluate_all_alerts(conn)
    except Exception:
        logger.exception("Alert evaluation failed")
        conn.rollback()
        return

    if not fired:
        logger.info("alerts: none fired")
        return

    emailed = sum(1 for event in fired if event.get("emailed"))
    tickers = ", ".join(sorted({event["ticker"] for event in fired}))
    logger.info("alerts: %s fired (%s emailed) across %s", len(fired), emailed, tickers)


def daily_job(skip_sentiment: bool = False) -> None:
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
            # Not an error. A fresh deployment with no users has nothing
            # to ingest, and saying so beats an empty log that reads like a
            # crash. Snapshots and alerts below still run: someone can hold
            # shares without watchlisting anything, and their equity curve
            # needs a point today either way.
            logger.info("No watchlisted tickers yet — nothing to ingest or predict. "
                        "Add one from the app, or run the ingestion scripts manually.")
    finally:
        conn.close()

    # These two open their own connections.
    if tickers:
        try:
            run_backtest()
        except Exception:
            logger.exception("Backtest step failed")

        try:
            run_daily_predictions()
        except Exception:
            logger.exception("Prediction step failed")

    # Snapshots and alerts run last, on a fresh connection, because both
    # depend on everything above: a snapshot should value holdings at the
    # close just ingested, and prediction_flip / confidence_above rules
    # compare against the prediction written moments ago.
    try:
        conn = psycopg2.connect(DB_URL)
    except Exception:
        logger.exception("Database unreachable — skipping snapshots and alerts")
        return

    try:
        snapshot_portfolios(conn)
        check_alerts(conn)
    finally:
        conn.close()

    logger.info("=== Daily cycle complete ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="FinCopilot daily job runner")
    parser.add_argument("--now", action="store_true",
                        help="Run the cycle once immediately and exit, instead of scheduling it")
    parser.add_argument("--skip-sentiment", action="store_true",
                        help="Skip FinBERT scoring for this run")
    args = parser.parse_args()

    if args.now:
        daily_job(skip_sentiment=args.skip_sentiment)
        return

    scheduler = BlockingScheduler()

    # Default 6am server time — after the close, and after most free data
    # providers have published the prior session. Match SCHEDULER_HOUR to
    # your provider's actual update schedule and your server's timezone;
    # running before the bar is published just means a wasted cycle.
    scheduler.add_job(daily_job, "cron", hour=SCHEDULER_HOUR, minute=0)

    logger.info("Scheduler started — daily cycle fires at %02d:00 server time. "
                "Run with --now to trigger one immediately.", SCHEDULER_HOUR)
    scheduler.start()


if __name__ == "__main__":
    main()
