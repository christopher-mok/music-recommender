-- Tracks searched with no acceptable preview. Lets `previews` resume without
-- re-querying, and makes coverage gaps visible.
CREATE TABLE IF NOT EXISTS preview_misses (
  track_id     INTEGER NOT NULL,          -- recordings.id
  provider     TEXT NOT NULL,             -- 'deezer'
  reason       TEXT NOT NULL,             -- 'no_match' | 'error'
  detail       TEXT,
  checked_at   TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (track_id, provider)
);

-- Clips that failed to embed for a given spec (decode error, OOM, missing file).
CREATE TABLE IF NOT EXISTS embedding_failures (
  spec_hash    TEXT NOT NULL,
  input_hash   TEXT NOT NULL,
  error        TEXT,
  failed_at    TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (spec_hash, input_hash)
);
