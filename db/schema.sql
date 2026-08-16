CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sector TEXT,
    cik TEXT
);

CREATE TABLE IF NOT EXISTS filings (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    filing_type TEXT NOT NULL,
    filing_date DATE NOT NULL,
    source_url TEXT NOT NULL,
    raw_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, filing_type, filing_date)
);

CREATE TABLE IF NOT EXISTS filing_chunks (
    id SERIAL PRIMARY KEY,
    filing_id INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),
    embedding_model TEXT,
    section_label TEXT,
    chunk_index INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (filing_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS price_history (
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    date DATE NOT NULL,
    open NUMERIC(12, 4),
    high NUMERIC(12, 4),
    low NUMERIC(12, 4),
    close NUMERIC(12, 4),
    volume BIGINT,
    PRIMARY KEY (ticker, date)
);

CREATE TABLE IF NOT EXISTS news_articles (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    published_date DATE NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    source TEXT,
    sentiment_score NUMERIC(6, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, published_date, headline)
);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    date DATE NOT NULL,
    source TEXT NOT NULL,
    score NUMERIC(6, 4),
    article_count INTEGER NOT NULL DEFAULT 0,
    raw_text_snippet TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, date, source)
);

CREATE TABLE IF NOT EXISTS model_versions (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    feature_set TEXT NOT NULL,
    trained_at TIMESTAMPTZ,
    train_ticker TEXT,
    test_accuracy NUMERIC(6, 4),
    test_sharpe NUMERIC(8, 4),
    checkpoint_path TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    prediction_date DATE NOT NULL,
    target_date DATE NOT NULL,
    predicted_direction TEXT NOT NULL CHECK (predicted_direction IN ('up', 'down')),
    confidence NUMERIC(5, 4) NOT NULL,
    prob_up NUMERIC(6, 5),
    actual_direction TEXT CHECK (actual_direction IS NULL OR actual_direction IN ('up', 'down')),
    resolved_date DATE,
    model_version_id INTEGER NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, prediction_date, model_version_id)
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS watchlists (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS query_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    response_text TEXT NOT NULL,
    cited_chunk_ids INTEGER[],
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS wallets (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    cash_balance NUMERIC(14, 2) NOT NULL DEFAULT 100000.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS holdings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    shares NUMERIC(14, 6) NOT NULL,
    avg_cost_basis NUMERIC(12, 4) NOT NULL,
    UNIQUE (user_id, ticker)
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES companies(ticker),
    action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    shares NUMERIC(14, 6) NOT NULL,
    price_per_share NUMERIC(12, 4) NOT NULL,
    realized_pl NUMERIC(14, 4),
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    triggered_by_prediction BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS trade_explanations (
    id SERIAL PRIMARY KEY,
    transaction_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    explanation TEXT NOT NULL,
    prediction_id INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
    sentiment_score NUMERIC(6, 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    total_value NUMERIC(14, 2) NOT NULL,
    cash_value NUMERIC(14, 2) NOT NULL,
    holdings_value NUMERIC(14, 2) NOT NULL,
    UNIQUE (user_id, date)
);

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

INSERT INTO model_versions (name, description, feature_set)
VALUES('baseline', 'LSTM on price/technical features only', 'baseline'), ('augmented', 'LSTM on price/technical features + FinBERT sentiment', 'augmented') ON CONFLICT (name) DO NOTHING;