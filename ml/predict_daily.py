"""
predict_daily.py

Runs once per day (via the scheduler in scheduler.py, or manually).
For every ticker on any user's watchlist, and for every registered model
version:

1. Pulls recent price history + stored daily sentiment
2. Rebuilds the exact feature pipeline the checkpoint was trained with
3. Runs the model to get P(up), and logs it to `predictions`

Logging happens BEFORE we know the outcome — this is what makes the
later backtest honest. If you only recorded predictions you already knew
were right, the accuracy number would be meaningless.

Both models run every day, not just the active one. That's the entire
basis of the leaderboard: baseline and augmented predicting the same days
under the same conditions, scored on outcomes neither of them had seen.

Usage:
    python predict_daily.py
    python predict_daily.py --ticker AAPL --ticker MSFT
"""

import argparse
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import torch
from dotenv import load_dotenv

from features import build_price_features, merge_sentiment
from train_lstm import DirectionLSTM, apply_scaler

load_dotenv()

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/fincopilot")
CHECKPOINT_DIR = Path(__file__).parent / "checkpoints"

# Enough rows to warm up a 20-day moving average and a 26/9 MACD and
# still leave a full sequence window. 250 trading days ~= one year.
HISTORY_ROWS = 250


class NoModelsRegistered(RuntimeError):
    pass


# --- Loading ---------------------------------------------------------


def get_watchlisted_tickers(conn) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT ticker FROM watchlists ORDER BY ticker")
        return [row[0] for row in cur.fetchall()]
    finally:
        cur.close()


def get_model_versions(conn) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, feature_set, checkpoint_path, is_active, train_ticker "
            "FROM model_versions WHERE checkpoint_path IS NOT NULL ORDER BY id"
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    if not rows:
        raise NoModelsRegistered(
            "No trained model is registered. Train one first:\n"
            "    python ml/train_lstm.py --ticker AAPL"
        )

    return [
        {"id": r[0], "name": r[1], "feature_set": r[2], "checkpoint_path": r[3],
         "is_active": r[4], "train_ticker": r[5]}
        for r in rows
    ]


def resolve_checkpoint(model_name: str, ticker: str, registered_path: str) -> tuple[Path, bool]:
    """
    Returns (path, is_ticker_specific).

    A checkpoint trained on AAPL carries AAPL's feature scaling. Applied
    to a $40 stock, every price-scale feature lands several standard
    deviations from where the model ever saw one, and the output is not
    meaningfully a prediction. So a per-ticker checkpoint always wins, and
    falling back to another ticker's is reported rather than done quietly.
    """
    specific = CHECKPOINT_DIR / f"{model_name}_{ticker.upper()}.pt"
    if specific.exists():
        return specific, True
    return Path(registered_path), False


def load_model(checkpoint_path: Path):
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model = DirectionLSTM(
        input_size=len(ckpt["feature_columns"]),
        hidden_size=ckpt.get("hidden_size", 32),
    )
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt


def load_recent_prices(conn, ticker: str, rows: int = HISTORY_ROWS) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, open, high, low, close, volume FROM price_history "
            "WHERE ticker = %s AND close IS NOT NULL ORDER BY date DESC LIMIT %s",
            (ticker, rows),
        )
        fetched = list(reversed(cur.fetchall()))
    finally:
        cur.close()

    df = pd.DataFrame(fetched, columns=["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].fillna(0).astype(float)
    return df


def load_sentiment(conn, ticker: str, source: str = "news") -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT date, score FROM sentiment_scores WHERE ticker = %s AND source = %s "
            "ORDER BY date",
            (ticker, source),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    df = pd.DataFrame(rows, columns=["date", "score"])
    if df.empty:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"),
                             "score": pd.Series(dtype="float64")})
    df["date"] = pd.to_datetime(df["date"])
    df["score"] = df["score"].astype(float)
    return df


# --- Inference -------------------------------------------------------


def build_inference_features(price_df: pd.DataFrame, sentiment_df: pd.DataFrame) -> pd.DataFrame:
    """
    Same pipeline as training, with one deliberate difference: the final
    row is KEPT.

    Training drops it because its target_direction refers to a day that
    hasn't happened. At inference that final row is the entire point —
    it's the most recent close, and the one the prediction is made from.
    """
    features = build_price_features(price_df)
    return merge_sentiment(features, sentiment_df)


def predict_one(model, ckpt: dict, features_df: pd.DataFrame) -> float:
    """Returns P(up) for the next trading day."""
    seq_len = ckpt.get("sequence_length", 10)
    columns = ckpt["feature_columns"]

    missing = [c for c in columns if c not in features_df.columns]
    if missing:
        raise ValueError(f"Feature pipeline is missing columns the checkpoint needs: {missing}")

    scaled = apply_scaler(features_df, ckpt["scaler"])
    window = scaled[columns].values[-seq_len:].astype(np.float32)

    if len(window) < seq_len:
        raise ValueError(
            f"Only {len(window)} usable rows, need {seq_len}. Ingest more price history."
        )

    with torch.no_grad():
        prob_up = float(model(torch.tensor(window).unsqueeze(0)).item())
    return prob_up


def next_trading_day(after: date) -> date:
    """
    Next weekday. Market holidays aren't modelled — backtest_daily.py
    resolves against the first bar that actually appears after the
    prediction's baseline close, so a holiday shifts the resolution date
    without stranding the prediction as permanently unresolved.
    """
    nxt = after + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


# --- Storage ---------------------------------------------------------


def log_prediction(conn, ticker: str, model_version_id: int, prediction_date: date,
                   target_date: date, direction: str, confidence: float,
                   prob_up: float) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO predictions
                (ticker, prediction_date, target_date, predicted_direction,
                 confidence, prob_up, model_version_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, prediction_date, model_version_id) DO UPDATE
                SET target_date = EXCLUDED.target_date,
                    predicted_direction = EXCLUDED.predicted_direction,
                    confidence = EXCLUDED.confidence,
                    prob_up = EXCLUDED.prob_up
            """,
            (ticker, prediction_date, target_date, direction, confidence,
             prob_up, model_version_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# --- Orchestration ---------------------------------------------------


def run_daily_predictions(tickers: list[str] | None = None, as_of: date | None = None) -> dict:
    conn = psycopg2.connect(DB_URL)
    today = as_of or date.today()

    written = 0
    skipped = 0

    try:
        try:
            models = get_model_versions(conn)
        except NoModelsRegistered as exc:
            print(exc)
            return {"written": 0, "skipped": 0, "tickers": 0}

        targets = tickers or get_watchlisted_tickers(conn)
        if not targets:
            print("No watchlisted tickers — nothing to predict.")
            return {"written": 0, "skipped": 0, "tickers": 0}

        print(f"Predicting for {len(targets)} ticker(s) across {len(models)} model(s)...")

        # One load per model, reused across every ticker.
        loaded: dict[str, tuple] = {}

        for ticker in targets:
            ticker = ticker.upper()
            price_df = load_recent_prices(conn, ticker)
            if price_df.empty:
                print(f"  {ticker}: no price history — run "
                      f"ingestion/fetch_prices.py {ticker}")
                skipped += 1
                continue

            sentiment_df = load_sentiment(conn, ticker)
            features_df = build_inference_features(price_df, sentiment_df)

            last_close_date = price_df["date"].iloc[-1].date()
            target = next_trading_day(max(last_close_date, today))

            for m in models:
                path, specific = resolve_checkpoint(m["name"], ticker, m["checkpoint_path"])
                key = str(path)

                try:
                    if key not in loaded:
                        loaded[key] = load_model(path)
                    model, ckpt = loaded[key]

                    prob_up = predict_one(model, ckpt, features_df)
                except FileNotFoundError:
                    print(f"  {ticker} [{m['name']}]: checkpoint missing at {path}")
                    skipped += 1
                    continue
                except Exception as exc:
                    print(f"  {ticker} [{m['name']}]: skipped — {exc}")
                    skipped += 1
                    continue

                direction = "up" if prob_up > 0.5 else "down"
                confidence = prob_up if direction == "up" else 1.0 - prob_up

                log_prediction(conn, ticker, m["id"], today, target,
                               direction, confidence, prob_up)
                written += 1

                note = "" if specific else f"  [using {ckpt.get('train_ticker', '?')}-trained weights]"
                print(f"  {ticker} [{m['name']}]: {direction} "
                      f"({confidence:.1%} confidence, P(up)={prob_up:.3f}) "
                      f"for {target}{note}")

        print(f"\nWrote {written} prediction(s), skipped {skipped}.")
        return {"written": written, "skipped": skipped, "tickers": len(targets)}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Generate and log daily predictions")
    parser.add_argument("--ticker", action="append", default=[],
                        help="Predict only this ticker; repeatable. Defaults to all watchlisted.")
    args = parser.parse_args()

    run_daily_predictions(tickers=[t.upper() for t in args.ticker] or None)


if __name__ == "__main__":
    main()
