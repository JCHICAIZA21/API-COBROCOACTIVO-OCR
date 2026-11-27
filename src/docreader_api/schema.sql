-- Batch jobs of the docreader service. Idempotent: applied at startup by PostgresJobStore.

CREATE TABLE IF NOT EXISTS reading_jobs (
    id          uuid        PRIMARY KEY,
    client      text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reading_items (
    id          uuid        PRIMARY KEY,
    job_id      uuid        NOT NULL REFERENCES reading_jobs (id) ON DELETE CASCADE,
    position    int         NOT NULL,
    request     jsonb       NOT NULL,
    status      text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    result      jsonb,
    error       text,
    attempts    int         NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Job summary, result pages and the job_id foreign key all go through this index.
CREATE UNIQUE INDEX IF NOT EXISTS reading_items_job_position ON reading_items (job_id, position);

-- Unfinished items only (stays small): re-enqueue of orphan pending items, stuck "processing".
CREATE INDEX IF NOT EXISTS reading_items_open ON reading_items (status, updated_at)
    WHERE status IN ('pending', 'processing');

-- Retention: oldest jobs first.
CREATE INDEX IF NOT EXISTS reading_jobs_created_at ON reading_jobs (created_at);
