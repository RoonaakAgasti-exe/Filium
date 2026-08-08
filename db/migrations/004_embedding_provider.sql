-- 004_embedding_provider.sql
--
-- Records which model produced each stored vector.
--
-- Why: `filing_chunks.embedding` was declared `vector(1536)` — the width
-- of text-embedding-3-small — which hard-wired the one part of the app
-- that had no keyless fallback. Prices degrade Alpaca -> FMP -> Yahoo ->
-- stored close, and news degrades Finnhub -> NewsAPI -> Google RSS, but
-- retrieval simply did not exist without OpenAI billing: no key, no
-- vectors, no chunks, no chat. A local sentence-transformers model
-- (BAAI/bge-small-en-v1.5, 384 dims) closes that gap.
--
-- Vectors from different models are not comparable — different spaces,
-- and here different widths — so the app refuses to query a corpus
-- embedded by a model other than the active one rather than returning
-- nonsense rankings. That check needs to know what embedded each row,
-- which is what this column is for. See backend/embeddings.py.
--
-- This migration is additive and safe to run repeatedly. It does NOT
-- touch the column width, because the right width depends on a runtime
-- setting (EMBEDDING_PROVIDER) that SQL can't see. Ingestion owns that:
-- ingestion/chunk_and_embed.py widens or narrows the column to match the
-- active model, but only while the table is empty, so no migration ever
-- has to delete a corpus to change providers. Switching providers on a
-- populated database is a deliberate act:
--
--   TRUNCATE filing_chunks;                        -- then re-ingest
--   python ingestion/ingest_filing.py AAPL MSFT
--
-- Usage:
--   docker compose exec -T db psql -U postgres -d fincopilot \
--       < db/migrations/004_embedding_provider.sql

BEGIN;

ALTER TABLE filing_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;

COMMENT ON COLUMN filing_chunks.embedding_model IS
    'Model that produced `embedding`. Queries embedded by a different '
    'model are rejected rather than compared — see backend/embeddings.py.';

-- Anything already stored predates this column, and the only provider
-- that existed then was OpenAI's default. Labelling those rows keeps the
-- consistency check meaningful instead of treating an existing corpus as
-- "unknown, assume fine".
UPDATE filing_chunks
   SET embedding_model = 'text-embedding-3-small'
 WHERE embedding_model IS NULL;

COMMIT;
