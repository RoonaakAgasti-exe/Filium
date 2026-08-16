import os
from datetime import date
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")

def get_unresolved_predictions(conn, as_of:date) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, ticker, prediction_date, target_date
        FROM predictions
        WHERE target_date <= %s AND actual_direction IS NULL
        """, as_of)
    columns = ["id", "ticker", "prediction_date", "target_date"]
    rows = cur.fetchall()
    cur.close()
    return [dict(zip(columns, row)) for row in rows]

def get_actual_direction(conn, ticker:str, prediction_date:date, target_date:date) -> tuple[str, date] | None:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, close FROM price_history "
            "WHERE ticker = %s AND date <= %s AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1", (ticker, prediction_date))
        baseline = cur.fetchone()
        if baseline is None:
            return None
        baseline_date, baseline_close = baseline
        cur.execute(
            "SELECT date, close FROM price_history "
            "WHERE ticker = %s AND date > %s AND close IS NOT NULL "
            "ORDER BY date ASC LIMIT 1", (ticker, baseline_date))
        outcome = cur.fetchone()
    finally:
        cur.close()
    if outcome is None:
        return None
    outcome_date, outcome_close = outcome
    if outcome_date < target_date and outcome_date <= prediction_date:
        return None
    return ("up" if outcome_close > baseline_close else "down"), outcome_date

def update_actual_direction(conn, prediction_id:int, actual_direction:str, resolved_date:date):
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE predictions SET actual_direction = %s, resolved_date = %s "
            "WHERE id = %s", (actual_direction, resolved_date, prediction_id))
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
            outcome = get_actual_direction(conn, pred["ticker"], pred["prediction_date"], pred["target_date"])
            if outcome is None:
                print(f"  {pred['ticker']} ({pred['target_date']}): outcome bar not in price_history yet, skipping")
                continue
            actual, resolved_date = outcome
            update_actual_direction(conn, pred["id"], actual, resolved_date)
            resolved_count += 1
            late = "" if resolved_date == pred["target_date"] else f" [bar dated {resolved_date}]"
            print(f"  {pred['ticker']} ({pred['target_date']}): resolved as '{actual}'{late}")
        print(f"\nResolved {resolved_count}/{len(unresolved)} prediction(s).")
    finally:
        conn.close()

if __name__ == "__main__":
    run_backtest()