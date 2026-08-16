import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import psycopg2
import torch
import torch.nn as nn
from dotenv import load_dotenv
from torch.utils.data import Dataset, DataLoader
from features import (build_price_features, merge_sentiment, FEATURE_COLUMNS_BASELINE, FEATURE_COLUMNS_AUGMENTED)

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
SEQUENCE_LENGTH = 10
HIDDEN_SIZE = 32
NUM_EPOCHS = 30
LEARNING_RATE = 1e-3
BATCH_SIZE = 16
CHECKPOINT_DIR = Path(__file__).parent / "checkpoints"
MODEL_SPECS = [
    {
        "name": "baseline",
        "feature_set": "baseline",
        "columns": FEATURE_COLUMNS_BASELINE,
        "description": "LSTM on price and technical indicators only (RSI, MACD, moving averages, daily return)."
    },
    {
        "name": "augmented",
        "feature_set": "augmented",
        "columns": FEATURE_COLUMNS_AUGMENTED,
        "description": "Same LSTM plus the daily FinBERT news sentiment score."
    }]

class SequenceDataset(Dataset):
    def __init__(self, features_df:pd.DataFrame, feature_columns:list[str], seq_len:int):
        self.seq_len = seq_len
        self.X = features_df[feature_columns].values.astype(np.float32)
        self.y = features_df["target_direction"].values.astype(np.float32)

    def __len__(self):
        return max(0, len(self.X) - self.seq_len)

    def __getitem__(self, idx):
        seq = self.X[idx: idx + self.seq_len]
        label = self.y[idx + self.seq_len - 1]
        return torch.tensor(seq), torch.tensor(label)

class DirectionLSTM(nn.Module):
    def __init__(self, input_size: int, hidden_size: int = HIDDEN_SIZE):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first = True)
        self.fc = nn.Linear(hidden_size, 1)

    def forward(self, x):
        _, (h_n, _) = self.lstm(x)
        out = self.fc(h_n[-1])
        return torch.sigmoid(out).squeeze(-1)

def _query_df(conn, sql:str, params:tuple, columns:list[str]) -> pd.DataFrame:
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
    finally:
        cur.close()
    return pd.DataFrame(rows, columns = columns)

def load_price_history(conn, ticker:str) -> pd.DataFrame:
    df = _query_df(conn,
        "SELECT date, open, high, low, close, volume FROM price_history "
        "WHERE ticker = %s AND close IS NOT NULL ORDER BY date", ticker.upper(), ["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        raise SystemExit(f"No price history for {ticker.upper()}. Ingest it first:\n python ingestion/fetch_prices.py {ticker.upper()} --days 1825")
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].fillna(0).astype(float)
    return df

def load_sentiment(conn, ticker:str, source:str = "news") -> pd.DataFrame:
    df = _query_df(conn,
        "SELECT date, score FROM sentiment_scores "
        "WHERE ticker = %s AND source = %s ORDER BY date", (ticker.upper(), source), ["date", "score"])
    if df.empty:
        print(f"  ! no '{source}' sentiment stored for {ticker.upper()} — the augmented model will train on a constant-zero sentiment feature.")
        return pd.DataFrame({"date": pd.Series(dtype = "datetime64[ns]"), "score": pd.Series(dtype = "float64")})
    df["date"] = pd.to_datetime(df["date"])
    df["score"] = df["score"].astype(float)
    return df

def time_based_split(df:pd.DataFrame, train_frac:float = 0.7, val_frac:float = 0.15):
    n = len(df)
    train_end = int(n * train_frac)
    val_end = int(n * (train_frac + val_frac))
    return df.iloc[:train_end], df.iloc[train_end:val_end], df.iloc[val_end:]

def fit_scaler(train_df:pd.DataFrame, feature_columns:list[str]) -> dict:
    means = train_df[feature_columns].mean()
    stds = train_df[feature_columns].std().replace(0, 1.0).fillna(1.0)
    return {"columns":list(feature_columns), "mean":[float(means[c]) for c in feature_columns], "std":[float(stds[c]) for c in feature_columns]}

def apply_scaler(df:pd.DataFrame, scaler:dict) -> pd.DataFrame:
    out = df.copy()
    for col, mean, std in zip(scaler["columns"], scaler["mean"], scaler["std"]):
        out[col] = (out[col].astype(float) - mean) / (std or 1.0)
    return out

def train_model(train_df, val_df, feature_columns:list[str], epochs:int) -> DirectionLSTM:
    train_ds = SequenceDataset(train_df, feature_columns, SEQUENCE_LENGTH)
    val_ds = SequenceDataset(val_df, feature_columns, SEQUENCE_LENGTH)
    train_loader = DataLoader(train_ds, batch_size = BATCH_SIZE, shuffle = True)
    val_loader = DataLoader(val_ds, batch_size = BATCH_SIZE, shuffle = False)
    model = DirectionLSTM(input_size = len(feature_columns))
    optimizer = torch.optim.Adam(model.parameters(), lr = LEARNING_RATE)
    loss_fn = nn.BCELoss()
    for epoch in range(epochs):
        model.train()
        for X_batch, y_batch in train_loader:
            optimizer.zero_grad()
            preds = model(X_batch)
            loss = loss_fn(preds, y_batch)
            loss.backward()
            optimizer.step()
        if (epoch + 1) % 10 == 0 or epoch == 0:
            model.eval()
            with torch.no_grad():
                val_losses = [loss_fn(model(X), y).item() for X, y in val_loader]
            mean_val = np.mean(val_losses) if val_losses else float("nan")
            print(f"    epoch {epoch+1}/{epochs}  val_loss={mean_val:.4f}")
    return model

def evaluate_model(model:DirectionLSTM, test_df:pd.DataFrame, feature_columns:list[str]) -> dict:
    test_ds = SequenceDataset(test_df, feature_columns, SEQUENCE_LENGTH)
    test_loader = DataLoader(test_ds, batch_size = BATCH_SIZE, shuffle = False)
    model.eval()
    all_preds, all_labels = [], []
    with torch.no_grad():
        for X, y in test_loader:
            preds = model(X)
            all_preds.extend((preds > 0.5).float().tolist())
            all_labels.extend(y.tolist())
    if not all_preds:
        return {"accuracy":None, "sharpe":None, "num_predictions":0}
    accuracy = float(np.mean(np.array(all_preds) == np.array(all_labels)))
    returns = test_df["raw_daily_return"].values[SEQUENCE_LENGTH:]
    strategy_returns = returns[:len(all_preds)] * np.array(all_preds)
    sharpe = compute_sharpe(strategy_returns)
    return {"accuracy":accuracy, "sharpe":sharpe, "num_predictions":len(all_preds)}

def compute_sharpe(returns:np.ndarray, risk_free_rate:float = 0.0, periods_per_year:int = 252) -> float:
    if len(returns) < 2 or np.std(returns) == 0:
        return 0.0
    excess_returns = returns - (risk_free_rate / periods_per_year)
    return float(np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(periods_per_year))

def save_checkpoint(model:DirectionLSTM, spec:dict, scaler:dict, ticker:str, metrics:dict) -> Path:
    CHECKPOINT_DIR.mkdir(parents = True, exist_ok = True)
    path = CHECKPOINT_DIR / f"{spec['name']}_{ticker.upper()}.pt"
    torch.save({
            "state_dict": model.state_dict(),
            "model_name": spec["name"],
            "feature_set": spec["feature_set"],
            "feature_columns": spec["columns"],
            "sequence_length": SEQUENCE_LENGTH,
            "hidden_size": HIDDEN_SIZE,
            "scaler": scaler,
            "train_ticker": ticker.upper(),
            "metrics": metrics,
            "trained_at": datetime.now(timezone.utc).isoformat()}, path)
    return path

def register_model(conn, spec:dict, ticker:str, metrics:dict, checkpoint:Path, is_active:bool) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO model_versions
                (name, description, feature_set, trained_at, train_ticker,
                 test_accuracy, test_sharpe, checkpoint_path, is_active)
            VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET
                description = EXCLUDED.description,
                feature_set = EXCLUDED.feature_set,
                trained_at = EXCLUDED.trained_at,
                train_ticker = EXCLUDED.train_ticker,
                test_accuracy = EXCLUDED.test_accuracy,
                test_sharpe = EXCLUDED.test_sharpe,
                checkpoint_path = EXCLUDED.checkpoint_path,
                is_active = EXCLUDED.is_active
            RETURNING id
            """, (spec["name"], spec["description"], spec["feature_set"], ticker.upper(), metrics["accuracy"], metrics["sharpe"], str(checkpoint), is_active))
        model_id = cur.fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return model_id

def run_comparison(price_df:pd.DataFrame, sentiment_df:pd.DataFrame, epochs:int = NUM_EPOCHS) -> dict:
    features_df = build_price_features(price_df)
    features_df = merge_sentiment(features_df, sentiment_df)
    features_df = features_df.iloc[:-1]
    features_df = features_df.assign(raw_daily_return = features_df["daily_return"].astype(float))
    train_df, val_df, test_df = time_based_split(features_df)
    print(f"Split sizes — train:{len(train_df)}, val:{len(val_df)}, test:{len(test_df)}")
    smallest = min(len(train_df), len(val_df), len(test_df))
    if smallest <= SEQUENCE_LENGTH:
        raise SystemExit(f"Not enough history: the smallest split has {smallest} rows but the sequence length is {SEQUENCE_LENGTH}, so at least one split would produce zero training examples. Ingest more price history (--days 1825 gets ~5 years) or lower SEQUENCE_LENGTH.")
    results = {}
    for spec in MODEL_SPECS:
        print(f"\nTraining {spec['name'].upper()} model ({spec['feature_set']} features)...")
        scaler = fit_scaler(train_df, spec["columns"])
        tr, va, te = (apply_scaler(d, scaler) for d in (train_df, val_df, test_df))
        model = train_model(tr, va, spec["columns"], epochs)
        metrics = evaluate_model(model, te, spec["columns"])
        results[spec["name"]] = {"spec":spec, "model":model, "scaler":scaler, "metrics":metrics}
    baseline = results["baseline"]["metrics"]
    augmented = results["augmented"]["metrics"]
    print(f"\n{'='*52}")
    print(f"{'Metric':<20}{'Baseline':<16}{'Augmented':<16}")
    print(f"{'Accuracy':<20}{_fmt_pct(baseline['accuracy']):<16}{_fmt_pct(augmented['accuracy']):<16}")
    print(f"{'Sharpe':<20}{_fmt_num(baseline['sharpe']):<16}{_fmt_num(augmented['sharpe']):<16}")
    print(f"{'Test predictions':<20}{baseline['num_predictions']:<16}{augmented['num_predictions']:<16}")
    print("=" * 52)
    print("Report whichever number you actually got. An augmented model that fails to beat baseline is a real, publishable result — a suspiciously clean win is the thing an interviewer will pull on.")
    return results

def _fmt_pct(v):
    return "n/a" if v is None else f"{v:.1%}"

def _fmt_num(v):
    return "n/a" if v is None else f"{v:.2f}"

def main():
    parser = argparse.ArgumentParser(description = "Train and compare baseline vs sentiment-augmented LSTM")
    parser.add_argument("--ticker", required = True)
    parser.add_argument("--epochs", type = int, default = NUM_EPOCHS)
    parser.add_argument("--sentiment-source", default = "news", choices = ["news", "filing"])
    parser.add_argument("--no-register", action = "store_true", help = "Train and save checkpoints without touching model_versions")
    args = parser.parse_args()
    ticker = args.ticker.upper()
    conn = psycopg2.connect(DB_URL)
    try:
        price_df = load_price_history(conn, ticker)
        sentiment_df = load_sentiment(conn, ticker, args.sentiment_source)
        print(f"Loaded {len(price_df)} price rows and {len(sentiment_df)} sentiment day(s) for {ticker}.")
        results = run_comparison(price_df, sentiment_df, args.epochs)

        def score(name):
            acc = results[name]["metrics"]["accuracy"]
            return acc if acc is not None else -1.0

        winner = max(results, key = score)
        print()
        for name, r in results.items():
            path = save_checkpoint(r["model"], r["spec"], r["scaler"], ticker, r["metrics"])
            print(f"saved {name} checkpoint -> {path}")
            if not args.no_register:
                model_id = register_model(conn, r["spec"], ticker, r["metrics"], path, is_active = (name == winner))
                flag = " (active)" if name == winner else ""
                print(f"  registered as model_versions.id={model_id}{flag}")
        if not args.no_register:
            print(f"\nActive model: {winner}. Next: python ml/predict_daily.py")
    finally:
        conn.close()

if __name__ == "__main__":
    main()