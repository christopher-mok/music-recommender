-- Add the uniqueness needed by seed-selection upserts for databases created
-- before the constraint was added to migration 001.
DELETE FROM seed_selections
WHERE id NOT IN (
    SELECT MIN(id) FROM seed_selections GROUP BY job_id, name
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_selections_job_name
    ON seed_selections(job_id, name);
