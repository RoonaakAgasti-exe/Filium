import pandas as pd
import numpy as np

def compute_rsi(close:pd.Series, period:int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window = period, min_periods = period).mean()
    avg_loss = loss.rolling(window = period, min_periods = period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def compute_macd(close:pd.Series, fast:int = 12, slow:int = 26, signal:int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span = fast, adjust = False).mean()
    ema_slow = close.ewm(span = slow, adjust = False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span = signal, adjust = False).mean()
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line})

def compute_moving_averages(close:pd.Series, windows:list[int] = [5, 10, 20]) -> pd.DataFrame:
    return pd.DataFrame({f"ma_{w}":close.rolling(window = w, min_periods = 1).mean() for w in windows})

def build_price_features(price_df:pd.DataFrame) -> pd.DataFrame:
    df = price_df.copy().sort_values("date").reset_index(drop = True)
    df["rsi"] = compute_rsi(df["close"])
    macd_df = compute_macd(df["close"])
    df = pd.concat([df, macd_df], axis = 1)
    ma_df = compute_moving_averages(df["close"])
    df = pd.concat([df, ma_df], axis = 1)
    df["daily_return"] = df["close"].pct_change().fillna(0)
    df["target_direction"] = (df["close"].shift(-1) > df["close"]).astype(int)
    return df

def merge_sentiment(price_features_df:pd.DataFrame, sentiment_df:pd.DataFrame) -> pd.DataFrame:
    merged = price_features_df.merge(sentiment_df[["date", "score"]], on = "date", how = "left")
    merged = merged.rename(columns = {"score": "sentiment_score"})
    merged["sentiment_score"] = merged["sentiment_score"].fillna(0.0)
    return merged

FEATURE_COLUMNS_BASELINE = ["rsi", "macd", "macd_signal", "ma_5", "ma_10", "ma_20", "daily_return"]
FEATURE_COLUMNS_AUGMENTED = FEATURE_COLUMNS_BASELINE + ["sentiment_score"]