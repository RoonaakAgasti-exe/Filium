-- schema.sql
-- Full FinCopilot database schema (v2).
-- Run this once against a fresh PostgreSQL database with the pgvector
-- extension available (Postgres 14+ recommended).
--
-- Usage:
--   createdb fincopilot
--   psql fincopilot -f schema.sql
--
-- If you already have a v1 database, do NOT re-run this file — apply
-- db/migrations/002_feature_expansion.sql instead, which adds everything
-- v2 introduced without dropping your existing data.

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- Core reference data
-- ============================================================

CREATE TABLE IF NOT EXISTS companies (
    ticker      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    sector      TEXT,
    cik         TEXT
);

-- ============================================================
-- Filings + RAG
-- ============================================================

-- filing_type carries '10-K', '10-Q', '8-K' and — as of v2 — 'transcript'
-- for earnings call transcripts. Transcripts reuse this table (and the
-- whole chunk-and-embed pipeline) rather than getting their own, so RAG
-- retrieval spans filings and calls with no extra query paths.
CREATE TABLE IF NOT EXISTS filings (
    id          SERIAL PRIMARY KEY,
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    filing_type TEXT NOT NULL,
    filing_date DATE NOT NULL,
    source_url  TEXT NOT NULL,
    raw_text    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, filing_type, filing_date)
);

CREATE TABLE IF NOT EXISTS filing_chunks (
    id              SERIAL PRIMARY KEY,
    filing_id       INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    chunk_text      TEXT NOT NULL,
    embedding       vector(1536),        -- 1536 = OpenAI text-embedding-3-small dimension
    section_label   TEXT,
    chunk_index     INTEGER NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (filing_id, chunk_index)
);

-- NOTE ON THE VECTOR INDEX (do not skip this):
-- An ivfflat index is an *approximate* nearest-neighbor index. With few
-- rows (roughly under a few thousand) it can silently return fewer or
-- worse matches than an exact scan — confirmed while building this schema:
-- a 3-row test table returned only 1 result for a top-3 query until the
-- index was dropped. Do NOT create this index until you've loaded a
-- realistic amount of real filing data (low thousands of chunks or more).
-- Until then, plain `ORDER BY embedding <=> query_vector LIMIT k` without
-- any index does an exact scan and is both correct and fast enough at
-- this project's scale.
--
-- When you do add it later, set lists ~= sqrt(row_count):
-- CREATE INDEX filing_chunks_embedding_idx
--     ON filing_chunks
--     USING ivfflat (embedding vector_cosine_ops)
--     WITH (lists = 100);

-- ============================================================
-- Prices + news + sentiment + predictions
-- ============================================================

CREATE TABLE IF NOT EXISTS price_history (
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    date        DATE NOT NULL,
    open        NUMERIC(12, 4),
    high        NUMERIC(12, 4),
    low         NUMERIC(12, 4),
    close       NUMERIC(12, 4),
    volume      BIGINT,
    PRIMARY KEY (ticker, date)
);

-- Raw news headlines, stored before scoring so sentiment can be
-- recomputed (or a different model swapped in) without re-fetching.
CREATE TABLE IF NOT EXISTS news_articles (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    published_date  DATE NOT NULL,
    headline        TEXT NOT NULL,
    summary         TEXT,
    url             TEXT,
    source          TEXT,
    sentiment_score NUMERIC(6, 4),   -- per-article FinBERT score, NULL until scored
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, published_date, headline)
);

-- The UNIQUE constraint matters more than it looks: without it, re-running
-- the sentiment job inserts a second row for the same (ticker, date,
-- source), and the later merge onto price features fans out — silently
-- duplicating training rows and corrupting the model's input.
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id                  SERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    date                DATE NOT NULL,
    source              TEXT NOT NULL,   -- 'news', 'filing'
    score               NUMERIC(6, 4),   -- -1.0 to 1.0
    article_count       INTEGER NOT NULL DEFAULT 0,
    raw_text_snippet    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, date, source)
);

-- Tracks each trained model so baseline vs. sentiment-augmented can be
-- compared side by side on live accuracy, not just on the historical
-- test split (PDF: "Leaderboard / model versioning").
CREATE TABLE IF NOT EXISTS model_versions (
    id                  SERIAL PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,   -- e.g. 'baseline', 'augmented'
    description         TEXT,
    feature_set         TEXT NOT NULL,          -- 'baseline' | 'augmented'
    trained_at          TIMESTAMPTZ,
    train_ticker        TEXT,
    test_accuracy       NUMERIC(6, 4),
    test_sharpe         NUMERIC(8, 4),
    checkpoint_path     TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- prob_up is the model's raw P(up) output. `confidence` stays as the
-- probability assigned to whichever direction was predicted, which is
-- what the UI shows — but calibration curves need the underlying
-- probability, not the folded-over confidence, or the curve is wrong.
CREATE TABLE IF NOT EXISTS predictions (
    id                  SERIAL PRIMARY KEY,
    ticker              TEXT NOT NULL REFERENCES companies(ticker),
    prediction_date     DATE NOT NULL,   -- date the prediction was made
    target_date         DATE NOT NULL,   -- date being predicted for
    predicted_direction TEXT NOT NULL CHECK (predicted_direction IN ('up', 'down')),
    confidence          NUMERIC(5, 4) NOT NULL,
    prob_up             NUMERIC(6, 5),
    actual_direction    TEXT CHECK (actual_direction IN ('up', 'down', NULL)),
    -- The bar actual_direction was actually scored against. Usually
    -- target_date, but market holidays and late-arriving price data push
    -- it out — storing it keeps the accuracy record auditable instead of
    -- silently conflating a next-day resolution with a four-day-late one.
    resolved_date       DATE,
    model_version_id    INTEGER NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- model_version_id is part of the key (and NOT NULL) so the same day
    -- can hold one prediction per model. It has to be NOT NULL rather than
    -- nullable-with-a-default: Postgres treats NULLs as distinct in a
    -- UNIQUE constraint, so a nullable column here would let ON CONFLICT
    -- silently insert duplicates instead of upserting.
    UNIQUE (ticker, prediction_date, model_version_id)
);

-- ============================================================
-- Users + app state
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlists (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL REFERENCES companies(ticker),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS query_history (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_text      TEXT NOT NULL,
    response_text   TEXT NOT NULL,
    cited_chunk_ids INTEGER[],
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Alerts (PDF: "Alerts on watchlist" + "Custom alerts via NL")
-- ============================================================

-- One row per standing rule. `rule_type` keeps the evaluator a simple
-- dispatch rather than an expression interpreter; natural-language alert
-- requests are parsed into one of these shapes by the LLM, so an
-- unparseable request fails loudly at creation time instead of silently
-- never firing.
CREATE TABLE IF NOT EXISTS alerts (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    rule_type       TEXT NOT NULL CHECK (rule_type IN
                        ('prediction_flip', 'price_move', 'sentiment_below',
                         'sentiment_above', 'confidence_above')),
    threshold       NUMERIC(10, 4),
    natural_language TEXT,             -- the original phrasing, if NL-created
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    last_fired_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_events (
    id              SERIAL PRIMARY KEY,
    alert_id        INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL,
    message         TEXT NOT NULL,
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    emailed         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================
-- Paper trading
-- ============================================================

CREATE TABLE IF NOT EXISTS wallets (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    cash_balance    NUMERIC(14, 2) NOT NULL DEFAULT 100000.00,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS holdings (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    shares          NUMERIC(14, 6) NOT NULL,
    avg_cost_basis  NUMERIC(12, 4) NOT NULL,
    UNIQUE (user_id, ticker)
);

-- realized_pl is filled on sells only (NULL on buys). Storing it at
-- execution time is what makes per-trade win rate computable later —
-- reconstructing it from cost-basis history after the fact is lossy
-- once a position has been fully closed and the holding row deleted.
CREATE TABLE IF NOT EXISTS transactions (
    id                      SERIAL PRIMARY KEY,
    user_id                 INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker                  TEXT NOT NULL REFERENCES companies(ticker),
    action                  TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    shares                  NUMERIC(14, 6) NOT NULL,
    price_per_share         NUMERIC(12, 4) NOT NULL,
    realized_pl             NUMERIC(14, 4),
    executed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    triggered_by_prediction BOOLEAN NOT NULL DEFAULT FALSE
);

-- PDF: "Explain-the-trade" — the LLM rationale generated at execution
-- time, kept in its own table so a failed/absent LLM call can never
-- block or roll back the trade itself.
CREATE TABLE IF NOT EXISTS trade_explanations (
    id              SERIAL PRIMARY KEY,
    transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    explanation     TEXT NOT NULL,
    prediction_id   INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
    sentiment_score NUMERIC(6, 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    total_value     NUMERIC(14, 2) NOT NULL,
    cash_value      NUMERIC(14, 2) NOT NULL,
    holdings_value  NUMERIC(14, 2) NOT NULL,
    UNIQUE (user_id, date)
);

-- ============================================================
-- Helpful indexes for common query patterns
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_filings_ticker ON filings(ticker);
CREATE INDEX IF NOT EXISTS idx_filings_ticker_type_date ON filings(ticker, filing_type, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_filing_chunks_filing ON filing_chunks(filing_id);
CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date ON price_history(ticker, date);
CREATE INDEX IF NOT EXISTS idx_news_ticker_date ON news_articles(ticker, published_date);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date ON sentiment_scores(ticker, date);
CREATE INDEX IF NOT EXISTS idx_predictions_ticker_date ON predictions(ticker, prediction_date);
CREATE INDEX IF NOT EXISTS idx_predictions_model ON predictions(model_version_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, executed_at);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_user_date ON portfolio_snapshots(user_id, date);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_alert_events_user ON alert_events(user_id, is_read, created_at DESC);

-- ============================================================
-- Seed: the two model versions the training script fills in
-- ============================================================

INSERT INTO model_versions (name, description, feature_set)
VALUES
    ('baseline',  'LSTM on price/technical features only',            'baseline'),
    ('augmented', 'LSTM on price/technical features + FinBERT sentiment', 'augmented')
ON CONFLICT (name) DO NOTHING;
