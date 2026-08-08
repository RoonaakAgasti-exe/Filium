-- 005_fix_actual_direction_check.sql
--
-- Makes `predictions.actual_direction`'s CHECK constraint actually check
-- something.
--
-- The original constraint was written as:
--
--     CHECK (actual_direction IN ('up', 'down', NULL))
--
-- which reads like "up, down, or not yet resolved" but does not mean that.
-- `x IN (a, b, c)` expands to `x = a OR x = b OR x = c`, and `x = NULL` is
-- never TRUE or FALSE — it is NULL. So for a value the column should have
-- refused, say 'sideways', the expression evaluates to:
--
--     'sideways' = 'up'  ->  FALSE
--     'sideways' = 'down' -> FALSE
--     'sideways' = NULL   -> NULL
--     FALSE OR FALSE OR NULL -> NULL
--
-- and Postgres accepts a CHECK whose result is NULL (only an explicit FALSE
-- rejects a row). The constraint therefore permitted *every* value —
-- including typos and empty strings — while looking like it constrained the
-- column to three states.
--
-- Nothing in the app writes a bad value today: ml/backtest_daily.py derives
-- the string from a price comparison, so it is always 'up' or 'down'. That
-- is exactly why this went unnoticed, and exactly why it is worth fixing:
-- the constraint is the guard for the code that gets written next, and the
-- accuracy record in `predictions` is the one table whose integrity the
-- whole "honestly-tracked" claim rests on.
--
-- Safe to run repeatedly. Safe on populated databases: the new constraint is
-- strictly weaker than what the app already writes, so validation of existing
-- rows cannot fail unless something has already written a bad value — in
-- which case the ALTER will tell you, which is the point.
--
-- Usage:
--   docker compose exec -T db psql -U postgres -d fincopilot \
--       < db/migrations/005_fix_actual_direction_check.sql

BEGIN;

-- The auto-generated name Postgres gave the inline CHECK in schema.sql.
-- DROP ... IF EXISTS keeps this idempotent and keeps it from failing on a
-- database created after schema.sql was corrected (where the constraint is
-- already the right one but may carry the same name).
ALTER TABLE predictions
    DROP CONSTRAINT IF EXISTS predictions_actual_direction_check;

ALTER TABLE predictions
    ADD CONSTRAINT predictions_actual_direction_check
    CHECK (actual_direction IS NULL OR actual_direction IN ('up', 'down'));

COMMIT;

-- Verify (should raise "violates check constraint", not insert a row):
--
--   INSERT INTO predictions
--       (ticker, prediction_date, target_date, predicted_direction,
--        confidence, actual_direction, model_version_id)
--   VALUES ('TEST', current_date, current_date, 'up', 0.5, 'sideways', 1);
