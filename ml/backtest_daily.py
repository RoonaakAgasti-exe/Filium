"""
backtest_daily.py

Runs once per day, after that day's price data is available. Finds every
prediction whose target_date has now passed and whose actual_direction is
still unfilled, looks up what the price actually did, and fills it in.

This is what turns "the model predicts X" into "the model has been right
Y% of the time" — the honesty of the whole accuracy story depends on this
running consistently, not just when the numbers look good.

Usage:
    python backtest_daily.py
"""

import os
from datetime import date

import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")


def get_unresolved_predictions(conn, as_of: date) -> list[dict]:
    """
    Predictions whose target_date has already happened (<= as_of) but
    whose actual_direction hasn't been filled in yet.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, ticker, prediction_date, target_date
        FROM predictions
        WHERE target_date <= %s AND actual_direction IS NULL
        """,
        (as_of,),
    )
    columns = ["id", "ticker", "prediction_date", "target_date"]
    rows = cur.fetchall()
    cur.close()
    return [dict(zip(columns, row)) for row in rows]


def get_actual_direction(conn, ticker: str, prediction_date: date,
                         target_date: date) -> tuple[str, date] | None:
    """
    Resolves a prediction against the first real trading bar that follows
    the close it was made from.

    Returns (direction, resolved_date), or None when the outcome bar
    isn't in the database yet — don't guess, wait and retry tomorrow.

    Why not simply look up target_date's close: target_date is a guess
    made at prediction time (the next weekday), and market holidays mean
    that day frequently has no bar at all. Demanding an exact match left
    every prediction pointed at a holiday permanently unresolved, which
    doesn't just lose those rows — it biases the accuracy figure toward
    whatever the resolvable days happened to do.

    So the baseline is the last close at or before prediction_date (the
    most recent price the model could have seen), and the outcome is the
    next close strictly after it.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, close FROM price_history "
            "WHERE ticker = %s AND date <= %s AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (ticker, prediction_date),
        )
        baseline = cur.fetchone()
        if baseline is None:
            return None

        baseline_date, baseline_close = baseline

        cur.execute(
            "SELECT date, close FROM price_history "
            "WHERE ticker = %s AND date > %s AND close IS NOT NULL "
            "ORDER BY date ASC LIMIT 1",
            (ticker, baseline_date),
        )
        outcome = cur.fetchone()
    finally:
        cur.close()

    if outcome is None:
        return None

    outcome_date, outcome_close = outcome

    # A bar exists but predates the day being predicted for — that means
    # the ingestion job has run since, and this prediction was made
    # against stale prices. Resolving against it would score the model on
    # a day it wasn't predicting.
    if outcome_date < target_date and outcome_date <= prediction_date:
        return None

    return ("up" if outcome_close > baseline_close else "down"), outcome_date


def update_actual_direction(conn, prediction_id: int, actual_direction: str,
                            resolved_date: date):
    """
    Records both the outcome and the bar it was scored against.

    resolved_date is stored rather than discarded because it frequently
    isn't target_date — a holiday or a data gap pushes resolution out.
    Without it, a prediction scored against the next day and one scored
    against a bar four days later look identical in the accuracy record,
    and there's no way to audit after the fact which is which.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE predictions SET actual_direction = %s, resolved_date = %s "
            "WHERE id = %s",
            (actual_direction, resolved_date, prediction_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def run_backtest(as_of: date | None = None):
    as_of = as_of or date.today()
    conn = psycopg2.connect(DB_URL)

    try:
        unresolved = get_unresolved_predictions(conn, as_of)
        print(f"Found {len(unresolved)} unresolved prediction(s) as of {as_of}...")

        resolved_count = 0
        for pred in unresolved:
            outcome = get_actual_direction(
                conn, pred["ticker"], pred["prediction_date"], pred["target_date"]
            )
            if outcome is None:
                print(f"  {pred['ticker']} ({pred['target_date']}): "
                      f"outcome bar not in price_history yet, skipping")
                continue

            actual, resolved_date = outcome
            update_actual_direction(conn, pred["id"], actual, resolved_date)
            resolved_count += 1

            # Call out the mismatch rather than hiding it — a resolution
            # several days late is a data-freshness signal worth seeing.
            late = "" if resolved_date == pred["target_date"] else f" [bar dated {resolved_date}]"
            print(f"  {pred['ticker']} ({pred['target_date']}): resolved as '{actual}'{late}")

        print(f"\nResolved {resolved_count}/{len(unresolved)} prediction(s).")

    finally:
        conn.close()


if __name__ == "__main__":
    run_backtest()
