-- 003_prediction_resolved_date.sql
--
-- Adds predictions.resolved_date: the trading bar an outcome was actually
-- scored against.
--
-- Why: backtest_daily.py used to require a price row dated exactly
-- target_date. target_date is a guess made at prediction time (the next
-- weekday), so every prediction pointed at a market holiday stayed
-- unresolved forever — which doesn't just lose those rows, it biases the
-- reported accuracy toward whatever the resolvable days happened to do.
-- Resolution now walks to the first real bar after the baseline close,
-- and this column records which bar that was.
--
-- Safe to run more than once. Existing resolved rows keep their outcome
-- and get a NULL resolved_date — they were scored under the old
-- exact-match rule, which means resolved_date was target_date by
-- definition. Backfilling that is a separate, optional statement at the
-- bottom, left commented out so it's a deliberate choice rather than a
-- silent rewrite of history.
--
-- Usage:
--   psql "$DATABASE_URL" -f db/migrations/003_prediction_resolved_date.sql
--
-- Or against the compose stack:
--   docker compose exec -T db psql -U postgres -d fincopilot \
--       < db/migrations/003_prediction_resolved_date.sql

BEGIN;

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS resolved_date DATE;

COMMENT ON COLUMN predictions.resolved_date IS
    'Trading bar actual_direction was scored against; may be later than '
    'target_date when a holiday or a data gap delayed resolution.';

-- Optional backfill for rows resolved under the old exact-match rule,
-- where resolved_date was necessarily target_date. Uncomment only if you
-- want those historical rows to read as resolved-on-time rather than
-- unknown:
--
-- UPDATE predictions
-- SET resolved_date = target_date
-- WHERE actual_direction IS NOT NULL AND resolved_date IS NULL;

COMMIT;
