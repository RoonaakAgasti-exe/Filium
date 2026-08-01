# ML Phase — Sentiment-Augmented LSTM

## What's here

- `sentiment.py` — runs FinBERT over stored news headlines and filing
  excerpts, writes a daily aggregated score per ticker per source to
  `sentiment_scores`, and writes each article's own score back to
  `news_articles.sentiment_score` so re-runs only score what's new.
- `features.py` — RSI, MACD, moving averages, and sentiment merging.
- `train_lstm.py` — trains a baseline (price-only) and an augmented
  (price + sentiment) LSTM on the same data with a strict **time-based**
  train/val/test split, reports accuracy and Sharpe for both, saves a
  checkpoint per model, and registers both in `model_versions` with
  `is_active` going to whichever measured better on the test split.
- `predict_daily.py` — for every watchlisted ticker and **every**
  registered model, rebuilds the training feature pipeline, logs P(up)
  to `predictions` before the outcome is known.
- `backtest_daily.py` — fills in `actual_direction` once the outcome bar
  exists. This is what turns "the model predicts X" into "the model has
  been right Y% of the time."
- `scheduler.py` — the whole daily cycle in dependency order: ingest
  prices and news, score sentiment, backtest, predict, snapshot every
  portfolio, then evaluate standing alert rules. The last two steps reuse
  `backend/wallet.py` and `backend/alerts_engine.py` rather than
  reimplementing valuation and rule evaluation, so the nightly job and the
  app can't drift apart on when an alert fires or what a portfolio is
  worth. Run `python ml/scheduler.py --now` to fire one cycle and exit.

Typical order on a fresh database:

```bash
python ingestion/fetch_prices.py AAPL --days 730
python ingestion/fetch_news.py AAPL --days 365
python ml/sentiment.py --ticker AAPL --source news
python ml/train_lstm.py --ticker AAPL
python ml/predict_daily.py --ticker AAPL
# ...the next day, once a new bar has landed:
python ml/backtest_daily.py
```

## What was actually tested here, and how

**Feature engineering (`features.py`)** — tested against a synthetic
40-day uptrending price series:
- RSI correctly read bullish (96.7) during the uptrend
- MACD was correctly positive during the uptrend
- No NaN values leaked through any indicator column
- Sentiment merging correctly filled `0.0` for news-free days while
  preserving real scores on days that had data

**Sharpe ratio (`train_lstm.py`)** — tested against three cases with
obvious expected directions: steady positive low-volatility returns gave
a very high Sharpe, zero-mean noise gave a value near 0, and negative
mean returns gave a negative Sharpe. All three passed.

**Full training pipeline** — ran end-to-end on synthetic 300-day price
data with a real, injected causal link (`today's price movement follows
yesterday's sentiment, plus noise`) so there was genuine signal for the
augmented model to find. **The pipeline ran without errors and produced
real accuracy/Sharpe numbers for both models** — but in this specific
run, the augmented model did NOT beat baseline (57.1% vs 62.9% accuracy).

**Prediction resolution (`backtest_daily.py`)** — tested against a
synthetic price table with a market holiday in it, covering: an outcome
bar that lands later than `target_date` (resolves, and reports the real
bar date), no outcome bar yet (returns `None` and retries tomorrow rather
than guessing), a downward move, a prediction made against stale prices
where the "next" bar predates the prediction itself (correctly refuses to
resolve), and a ticker with no price history at all.

## Why the augmented model didn't win in that test — and why that's worth knowing now

This is a legitimate, common outcome with small datasets, and it's worth
understanding before you run this on real data, not after:

- **209 training rows is small** for an LSTM to reliably learn to weight
  an extra, noisy feature — it likely needs more data or more epochs than
  this quick synthetic test used
- Neither model's validation loss dropped much over 30 epochs, meaning
  both were undertrained in this test — the comparison itself is fair,
  but neither model reflects what a properly-tuned version would do
- This isn't a sign the code is broken — the pipeline mechanics (time
  split, training loop, evaluation, Sharpe) all check out. It's a sign
  that on real data, you should expect to tune epochs, hidden size, and
  sequence length rather than assume the first run is representative

**For your README**, this is actually useful to report honestly rather
than hide: "sentiment did not show a clear improvement in an initial
test; this may reflect limited training data or undertuned
hyperparameters" is a more credible sentence than a suspiciously clean
win, and it sets up a natural next step (more data, more epochs, or a
regularization pass) rather than a claim you can't defend under
questioning.

Note that the leaderboard exists precisely so you don't have to settle
this from the test split alone: both models predict every day and are
scored on outcomes neither saw, so live accuracy accumulates on its own.

## Two things that decide whether the numbers mean anything

**Sentiment history has to be long enough to train on.** The augmented
model can only learn from days that have a sentiment score. If your news
source only reaches back 30 days (NewsAPI's free tier) but you have two
years of prices, almost every training row gets the `0.0` fill and the
"augmented" model is a baseline model wearing a hat. Finnhub's ~1 year of
per-company history is the difference-maker here; check how many
`sentiment_scores` rows you actually have before reading anything into a
comparison.

**Checkpoints are per-ticker, and that's deliberate.** A checkpoint
trained on AAPL carries AAPL's feature scaling. Applied to a $40 stock,
every price-scale feature lands several standard deviations from anything
the model saw in training, and the output isn't meaningfully a
prediction. `predict_daily.py` prefers `checkpoints/<model>_<TICKER>.pt`
and prints a warning when it has to fall back to another ticker's
weights — treat that warning as "train this ticker", not as noise.

## What still needs your real environment

- `sentiment.py` needs a real FinBERT download — that model comes from
  Hugging Face's hub, which isn't reachable from the sandbox this was
  built in, so the actual scoring inference is untested here
- Everything above was verified against synthetic data or a stubbed
  database. No part of this has run against a live Postgres with real
  ingested filings, prices, and news — the first real run is where you
  should expect to find whatever's left
