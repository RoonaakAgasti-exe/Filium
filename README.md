# FinCopilot

**AI research assistant for SEC filings, with a sentiment-augmented price
predictor and paper trading.** Ask a company a question, get an answer
sourced from their real filings. See a daily up/down signal with an
honestly-tracked accuracy record. Trade on paper — real prices, fake
money — and see whether the model's calls actually help.

> For the plain-language "explain it to a child" version of what this
> project does and how it works, see [`EXPLAINER.md`](EXPLAINER.md).
> This README is the technical/portfolio-facing version.

---

## Results

*(Fill this in after running `eval/label_retrieval.py`, `eval/score_retrieval.py`,
`eval/judge_faithfulness.py`, and `ml/train_lstm.py` against real ingested
data — see `eval/README.md` and `ml/README.md` for exact steps. Don't
ship this section with placeholder numbers; an honest lower number here
is worth more than a suspiciously perfect one.)*

| Metric | Result |
|---|---|
| Retrieval precision@5 (human-labeled, N=___ questions) | ___% |
| Answer faithfulness (LLM-as-judge) | ___% |
| Baseline LSTM directional accuracy | ___% |
| Sentiment-augmented LSTM directional accuracy | ___% |
| Backtested Sharpe ratio (baseline vs. augmented) | ___ vs ___ |
| Paper portfolio return vs. S&P 500 (over ___ days) | ___% vs ___% |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        FRONTEND                          │
│      (React — chat, predictions dashboard, portfolio)      │
└───────────────────────────┬───────────────────────────────┘
                             │ HTTP
┌───────────────────────────▼───────────────────────────────┐
│                        BACKEND                             │
│                       (FastAPI)                              │
│  /query  /predictions  /watchlist  /auth  /trade  /portfolio  │
└──────┬───────────────────────────────┬──────────────────┬────┘
       │                               │                    │
┌──────▼──────────┐         ┌─────────▼─────────┐  ┌───────▼────────┐
│    DATABASE       │         │   AI / ML             │  │   ALPACA          │
│  PostgreSQL +        │         │  - OpenAI embeddings   │  │  Paper Trading API │
│  pgvector              │         │  - LLM (RAG answers)     │  │  (real prices,      │
│  filings, chunks,        │         │  - FinBERT sentiment       │  │   fake money)         │
│  prices, predictions,      │         │  - LSTM (direction)           │  └────────────────────┘
│  wallets, transactions,      │         └───────────────────────────┘
│  portfolio_snapshots           │
└─────────┬─────────────────────┘
          │
┌─────────▼───────────┐        ┌──────────────────┐
│   INGESTION            │        │   SCHEDULER          │
│  SEC EDGAR, price/news    │        │  daily predictions +   │
│  fetch, chunk + embed       │        │  backtest scoring         │
└─────────────────────────────┘        └──────────────────────┘
```

## Repo structure

```
fincopilot/
├── ingestion/          # Phase 1-3: fetch, clean, chunk+embed filings, RAG query
├── db/schema.sql        # Phase 2: full Postgres schema (13 tables, pgvector)
├── eval/                 # Phase 4: retrieval precision + faithfulness eval harness
├── ml/                    # Phase 5-6: sentiment scoring, LSTM, daily prediction + backtest jobs
├── backend/                 # Phase 7-8: FastAPI app — auth, RAG, predictions, paper trading
├── frontend/                   # Phase 9: React app — chat, dashboard, portfolio/trade UI
├── docker-compose.yml            # Phase 10: 4-service deployment (db, backend, frontend, scheduler)
├── DEPLOYMENT.md                   # Phase 10: local Docker testing + Railway/Fly.io deploy steps
└── .env.example                      # every environment variable the project needs, in one place
```

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React (Vite) | Fast dev loop, good charting ecosystem (recharts) |
| Backend | FastAPI | Async-friendly, plays well with ML code, auto-generated docs |
| Database | PostgreSQL + pgvector | One database for relational data and vector search — no separate vector DB to keep in sync |
| Embeddings/LLM | OpenAI (`text-embedding-3-small`, `gpt-4o-mini`) | Cheap enough to run a real eval harness against |
| Sentiment | FinBERT (pretrained, inference-only) | Purpose-built for financial text tone |
| Prediction | LSTM (PyTorch), price + sentiment features | Extends an existing baseline rather than starting from scratch |
| Trading | Alpaca Paper Trading API | Real market prices and order mechanics with zero real-money risk or licensing burden |
| Deployment | Docker Compose → Railway/Fly.io | Free-tier friendly, one command to spin up all four services |

## Quickstart

```bash
git clone <your-repo-url> && cd fincopilot
cp .env.example .env   # fill in real API keys — see comments in the file for where to get each one

# 1. Ingest a filing
pip install -r ingestion/requirements.txt
python ingestion/fetch_filing.py AAPL
python ingestion/clean_filing.py data/raw/AAPL_10-K_<date>.html
python ingestion/chunk_and_embed.py data/processed/AAPL_10-K_<date>.json \
    --ticker AAPL --filing-type 10-K --filing-date <date> --source-url <url>

# 2. Run everything with Docker Compose
docker compose up --build
```

Then visit `http://localhost` for the app and `http://localhost:8000/docs`
for the interactive API reference.

For the full phase-by-phase build process (what each part does, why it
was built this way, and what was tested along the way), see the
individual READMEs in `eval/`, `ml/`, and `DEPLOYMENT.md`.

## Honest limitations

- This project is about demonstrating sound ML/engineering practice, not
  about actually beating the market — predicting stock direction is
  genuinely hard, and the README results section should reflect real,
  possibly modest numbers rather than an inflated claim
- **Paper trading only.** No real brokerage account, no real money, and
  this should never be presented as investment advice
- The LLM-as-judge faithfulness metric is exactly that — LLM-graded, not
  human-graded — and is labeled as such wherever it's reported
- Free-tier data APIs (SEC EDGAR, news, price data) rate-limit
  aggressively; ingesting many tickers at once will be slow by design
