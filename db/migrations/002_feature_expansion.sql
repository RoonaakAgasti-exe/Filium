-- 002_feature_expansion.sql
--
-- Migrates a v1 FinCopilot database up to the v2 schema (db/schema.sql)
-- without dropping data. Safe to run more than once.
--
-- Usage:
--   psql "$DATABASE_URL" -f db/migrations/002_feature_expansion.sql
--
-- Or against the compose stack:
--   docker compose exec -T db psql -U postgres -d fincopilot \
--       < db/migrations/002_feature_expansion.sql

BEGIN;

-- ------------------------------------------------------------
-- Model versioning (must come first — predictions references it)
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS model_versions (
    id                  SERIAL PRIMARY KEY,
    name                TEXT UNIQUE NOT NULL,
    description         TEXT,
    feature_set         TEXT NOT NULL,
    trained_at          TIMESTAMPTZ,
    train_ticker        TEXT,
    test_accuracy       NUMERIC(6, 4),
    test_sharpe         NUMERIC(8, 4),
    checkpoint_path     TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO model_versions (name, description, feature_set)
VALUES
    ('baseline',  'LSTM on price/technical features only',                'baseline'),
    ('augmented', 'LSTM on price/technical features + FinBERT sentiment', 'augmented')
ON CONFLICT (name) DO NOTHING;

-- ------------------------------------------------------------
-- predictions: prob_up + model_version_id
-- ------------------------------------------------------------

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS prob_up NUMERIC(6, 5);
ALTER TABLE predictions ADD COLUMN IF NOT EXISTS model_version_id INTEGER;

-- Existing rows predate model tracking; attribute them to 'augmented',
-- which is what the original single-model pipeline was meant to be.
UPDATE predictions
SET model_version_id = (SELECT id FROM model_versions WHERE name = 'augmented')
WHERE model_version_id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'predictions'
          AND column_name = 'model_version_id'
          AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE predictions ALTER COLUMN model_version_id SET NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'predictions_model_version_id_fkey'
    ) THEN
        ALTER TABLE predictions
            ADD CONSTRAINT predictions_model_version_id_fkey
            FOREIGN KEY (model_version_id) REFERENCES model_versions(id) ON DELETE CASCADE;
    END IF;

    -- Swap the old 2-column uniqueness for the 3-column one so the same
    -- day can hold one prediction per model version.
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'predictions_ticker_prediction_date_key'
    ) THEN
        ALTER TABLE predictions DROP CONSTRAINT predictions_ticker_prediction_date_key;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'predictions_ticker_prediction_date_model_version_id_key'
    ) THEN
        ALTER TABLE predictions
            ADD CONSTRAINT predictions_ticker_prediction_date_model_version_id_key
            UNIQUE (ticker, prediction_date, model_version_id);
    END IF;
END $$;

-- ------------------------------------------------------------
-- sentiment_scores: dedup, then enforce uniqueness
-- ------------------------------------------------------------

ALTER TABLE sentiment_scores ADD COLUMN IF NOT EXISTS article_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sentiment_scores ALTER COLUMN score TYPE NUMERIC(6, 4);

-- Any pre-existing duplicates have to go before the constraint can be
-- added; keep the most recently created row for each key.
DELETE FROM sentiment_scores s
USING sentiment_scores dup
WHERE s.ticker = dup.ticker
  AND s.date = dup.date
  AND s.source = dup.source
  AND s.id < dup.id;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'sentiment_scores_ticker_date_source_key'
    ) THEN
        ALTER TABLE sentiment_scores
            ADD CONSTRAINT sentiment_scores_ticker_date_source_key
            UNIQUE (ticker, date, source);
    END IF;
END $$;

-- ------------------------------------------------------------
-- filing_chunks: guard against double-ingesting the same filing
-- ------------------------------------------------------------

DELETE FROM filing_chunks c
USING filing_chunks dup
WHERE c.filing_id = dup.filing_id
  AND c.chunk_index = dup.chunk_index
  AND c.id < dup.id;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'filing_chunks_filing_id_chunk_index_key'
    ) THEN
        ALTER TABLE filing_chunks
            ADD CONSTRAINT filing_chunks_filing_id_chunk_index_key
            UNIQUE (filing_id, chunk_index);
    END IF;
END $$;

-- ------------------------------------------------------------
-- transactions: realized P&L for per-trade win rate
-- ------------------------------------------------------------

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS realized_pl NUMERIC(14, 4);

-- ------------------------------------------------------------
-- New tables
-- ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS news_articles (
    id              SERIAL PRIMARY KEY,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    published_date  DATE NOT NULL,
    headline        TEXT NOT NULL,
    summary         TEXT,
    url             TEXT,
    source          TEXT,
    sentiment_score NUMERIC(6, 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, published_date, headline)
);

CREATE TABLE IF NOT EXISTS alerts (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ticker          TEXT NOT NULL REFERENCES companies(ticker),
    rule_type       TEXT NOT NULL CHECK (rule_type IN
                        ('prediction_flip', 'price_move', 'sentiment_below',
                         'sentiment_above', 'confidence_above')),
    threshold       NUMERIC(10, 4),
    natural_language TEXT,
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

CREATE TABLE IF NOT EXISTS trade_explanations (
    id              SERIAL PRIMARY KEY,
    transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
    explanation     TEXT NOT NULL,
    prediction_id   INTEGER REFERENCES predictions(id) ON DELETE SET NULL,
    sentiment_score NUMERIC(6, 4),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_filings_ticker_type_date ON filings(ticker, filing_type, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_filing_chunks_filing ON filing_chunks(filing_id);
CREATE INDEX IF NOT EXISTS idx_news_ticker_date ON news_articles(ticker, published_date);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date ON sentiment_scores(ticker, date);
CREATE INDEX IF NOT EXISTS idx_predictions_model ON predictions(model_version_id);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_alert_events_user ON alert_events(user_id, is_read, created_at DESC);

COMMIT;
