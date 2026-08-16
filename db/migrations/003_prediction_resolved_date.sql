BEGIN;

ALTER TABLE predictions ADD COLUMN IF NOT EXISTS resolved_date DATE;
COMMENT ON COLUMN predictions.resolved_date IS
    'Trading bar actual_direction was scored against; may be later than '
    'target_date when a holiday or a data gap delayed resolution.';

COMMIT;