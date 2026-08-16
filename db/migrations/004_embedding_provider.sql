BEGIN;

ALTER TABLE filing_chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;
COMMENT ON COLUMN filing_chunks.embedding_model IS
    'Model that produced `embedding`. Queries embedded by a different '
    'model are rejected rather than compared — see backend/embeddings.py.';

UPDATE filing_chunks SET embedding_model = 'text-embedding-3-small' WHERE embedding_model IS NULL;
COMMIT;