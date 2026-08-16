# Filium Test Suite

```bash
pip install -r tests/requirements.txt
pytest tests/ -v
```

141 tests, no database and no network. Everything that reads from
Postgres is driven through a fake connection that answers each query
from canned rows.

## Coverage

- `test_analytics.py` — daily returns, Sharpe ratio, sample stdev
  (the annualized volatility figure), max drawdown, trade/win-rate stats,
  sector exposure, and the calibration curve (ECE + Brier score)
- `test_alerts_engine.py` — all five alert rule types, the once-per-day
  suppression check, and `evaluate_all_alerts` end to end
- `test_wallet.py` — buy/sell against a fake connection: weighted-average
  cost basis, realized P&L, the two rounding tolerances (a purchase that
  spends the balance exactly; a float-dust remainder that must close a
  position rather than strand it), rollback-without-commit on both
  insufficient-funds and insufficient-shares, the wallet-then-holding
  lock order, portfolio valuation with an unpriced holding, and snapshots
- `test_routers.py` — HTTP-level tests through `TestClient` with
  `get_conn` and `get_current_user_id` replaced via
  `app.dependency_overrides`: `/health` in both connected and degraded
  states, auth enforcement on every `/portfolio` route, response shapes,
  `?limit` bounds, all four `/portfolio/vs-benchmark` outcomes, and the
  exception handlers in `main.py` that map `ValueError` → 400 and
  `PriceUnavailable` → 503
- `test_backtest_sim.py` — backtest simulation logic

## Two things worth knowing before adding tests here

**Import backend modules by bare name, not `backend.x`.** `main.py` puts
`backend/` on `sys.path` and imports its siblings unqualified, so
`import db` and `import backend.db` produce two distinct module objects
holding two distinct `get_conn` function objects. `dependency_overrides`
is keyed on the function object, so overriding the wrong one is a silent
no-op and every authenticated request comes back 401. `conftest.py` puts
`backend/` on the path for this reason.

**Don't use `TestClient` as a context manager.** Entering it runs the
app's lifespan, which opens a real database pool. These tests want the
routes, not the startup.

## Not covered here

- `ml/scheduler.py`. Its steps delegate to functions tested here, but
  importing it pulls in the whole ML stack (torch, transformers), so CI
  covers it with a compile check rather than a unit test.
- The ML pipeline itself (`train_lstm.py`, `predict_daily.py`,
  `sentiment.py`) — see `ml/README.md` for what was verified there and
  how.
- Anything that genuinely needs a live Postgres or a real API key:
  concurrent `SELECT ... FOR UPDATE` behaviour under real contention,
  pgvector similarity search, and live Alpaca/OpenAI calls.
