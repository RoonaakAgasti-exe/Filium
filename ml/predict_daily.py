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
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
CHECKPOINT_DIR = Path(__file__).parent / "checkpoints"
HISTORY_ROWS = 250

class NoModelsRegistered(RuntimeError):
    pass

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
            "FROM model_versions WHERE checkpoint_path IS NOT NULL ORDER BY id")
        rows = cur.fetchall()
    finally:
        cur.close()
    if not rows:
        raise NoModelsRegistered(
            "No trained model is registered. Train one first: python ml/train_lstm.py --ticker AAPL")
    return [{"id": r[0], "name":r[1], "feature_set":r[2], "checkpoint_path":r[3], "is_active":r[4], "train_ticker":r[5]} for r in rows]

def resolve_checkpoint(model_name:str, ticker:str, registered_path:str) -> tuple[Path, bool]:
    specific = CHECKPOINT_DIR / f"{model_name}_{ticker.upper()}.pt"
    if specific.exists():
        return specific, True
    return Path(registered_path), False

def load_model(checkpoint_path:Path):
    ckpt = torch.load(checkpoint_path, map_location = "cpu", weights_only = False)
    model = DirectionLSTM(input_size = len(ckpt["feature_columns"]), hidden_size = ckpt.get("hidden_size", 32))
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    return model, ckpt

def load_recent_prices(conn, ticker:str, rows:int = HISTORY_ROWS, as_of:date | None = None) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        sql = ("SELECT date, open, high, low, close, volume FROM price_history "
               "WHERE ticker = %s AND close IS NOT NULL")
        params: list = [ticker.upper()]
        if as_of is not None:
            sql += " AND date <= %s"
            params.append(as_of)
        sql += " ORDER BY date DESC LIMIT %s"
        params.append(rows)
        cur.execute(sql, tuple(params))
        fetched = list(reversed(cur.fetchall()))
    finally:
        cur.close()
    df = pd.DataFrame(fetched, columns = ["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].fillna(0).astype(float)
    return df

def load_sentiment(conn, ticker:str, source:str = "news", as_of:date | None = None) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        sql = ("SELECT date, score FROM sentiment_scores "
               "WHERE ticker = %s AND source = %s")
        params: list = [ticker.upper(), source]
        if as_of is not None:
            sql += " AND date <= %s"
            params.append(as_of)
        sql += " ORDER BY date"
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    finally:
        cur.close()
    df = pd.DataFrame(rows, columns = ["date", "score"])
    if df.empty:
        return pd.DataFrame({"date":pd.Series(dtype = "datetime64[ns]"), "score":pd.Series(dtype = "float64")})
    df["date"] = pd.to_datetime(df["date"])
    df["score"] = df["score"].astype(float)
    return df

def build_inference_features(price_df:pd.DataFrame, sentiment_df:pd.DataFrame) -> pd.DataFrame:
    features = build_price_features(price_df)
    return merge_sentiment(features, sentiment_df)

def predict_one(model, ckpt:dict, features_df:pd.DataFrame) -> float:
    seq_len = ckpt.get("sequence_length", 10)
    columns = ckpt["feature_columns"]
    missing = [c for c in columns if c not in features_df.columns]
    if missing:
        raise ValueError(f"Feature pipeline is missing columns the checkpoint needs: {missing}")
    scaled = apply_scaler(features_df, ckpt["scaler"])
    window = scaled[columns].values[-seq_len:].astype(np.float32)
    if len(window) < seq_len:
        raise ValueError(f"Only {len(window)} usable rows, need {seq_len}. Ingest more price history.")
    with torch.no_grad():
        prob_up = float(model(torch.tensor(window).unsqueeze(0)).item())
    return prob_up

def next_trading_day(after:date) -> date:
    nxt = after + timedelta(days = 1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days = 1)
    return nxt

def log_prediction(conn, ticker:str, model_version_id:int, prediction_date:date, target_date:date, direction:str, confidence:float, prob_up:float) -> None:
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
            """, (ticker, prediction_date, target_date, direction, confidence, prob_up, model_version_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

def run_daily_predictions(tickers:list[str] | None = None, as_of:date | None = None) -> dict:
    conn = psycopg2.connect(DB_URL)
    today = as_of or date.today()
    written = 0
    skipped = 0
    try:
        try:
            models = get_model_versions(conn)
        except NoModelsRegistered as exc:
            print(exc)
            return {"written":0, "skipped":0, "tickers":0}
        targets = tickers or get_watchlisted_tickers(conn)
        if not targets:
            print("No watchlisted tickers — nothing to predict.")
            return {"written":0, "skipped":0, "tickers":0}
        print(f"Predicting for {len(targets)} ticker(s) across {len(models)} model(s)...")
        loaded: dict[str, tuple] = {}
        for ticker in targets:
            ticker = ticker.upper()
            price_df = load_recent_prices(conn, ticker, as_of = today)
            if price_df.empty:
                print(f"  {ticker}: no price history — run ingestion/fetch_prices.py {ticker}")
                skipped += 1
                continue
            sentiment_df = load_sentiment(conn, ticker, as_of = today)
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
                log_prediction(conn, ticker, m["id"], today, target, direction, confidence, prob_up)
                written += 1
                note = "" if specific else f"  [using {ckpt.get('train_ticker', '?')}-trained weights]"
                print(f"  {ticker} [{m['name']}]:{direction}, {confidence:.1%} confidence, P(up) = {prob_up:.3f}) for {target}{note}")
        print(f"\nWrote {written} prediction(s), skipped {skipped}.")
        return {"written":written, "skipped":skipped, "tickers":len(targets)}
    finally:
        conn.close()

def backfill_predictions(tickers:list[str] | None = None, days:int = 60) -> dict:
    today = date.today()
    totals = {"written":0, "skipped":0, "days":0}
    for offset in range(days, 0, -1):
        as_of = today - timedelta(days = offset)
        if as_of.weekday() >= 5:
            continue
        print(f"\n--- replaying {as_of} ---")
        result = run_daily_predictions(tickers = tickers, as_of = as_of)
        totals["written"] += result["written"]
        totals["skipped"] += result["skipped"]
        totals["days"] += 1
    print(f"\nBackfill complete: {totals['written']} prediction(s) across {totals['days']} trading day(s).")
    return totals

def main():
    parser = argparse.ArgumentParser(description = "Generate and log daily predictions")
    parser.add_argument("--ticker", action = "append", default = [], help = "Predict only this ticker; repeatable. Defaults to all watchlisted.")
    parser.add_argument("--backfill", type = int, metavar = "DAYS", help = "Also replay the last DAYS days so the leaderboard and track record have history immediately. Each replayed day only sees data available at its own close.")
    args = parser.parse_args()
    tickers = [t.upper() for t in args.ticker] or None
    if args.backfill:
        backfill_predictions(tickers = tickers, days = args.backfill)
    run_daily_predictions(tickers = tickers)

if __name__ == "__main__":
    main()