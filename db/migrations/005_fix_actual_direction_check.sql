BEGIN;

ALTER TABLE predictions DROP CONSTRAINT IF EXISTS predictions_actual_direction_check;
ALTER TABLE predictions ADD CONSTRAINT predictions_actual_direction_check CHECK (actual_direction IS NULL OR actual_direction IN ('up', 'down'));

COMMIT;