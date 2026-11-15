-- ============================================================
-- music_data Pipeline - Embeddings Schema
-- SQLite 3.35+ | Compatible with 001_initial_schema
-- ============================================================

-- Track-level audio clips (content-addressed, private cache)
CREATE TABLE IF NOT EXISTS audio_clips (
  audio_sha256   TEXT PRIMARY KEY,      -- hash of decoded PCM
  track_id       INTEGER NOT NULL,      -- references recordings.id
  mbid_track     TEXT,
  provider       TEXT,                  -- 'deezer' for now
  provider_track_id TEXT,               -- provider's internal track ID
  preview_url    TEXT,
  duration_s     REAL,
  fetched_at     TEXT DEFAULT (datetime('now')),
  FOREIGN KEY (track_id) REFERENCES recordings(id) ON DELETE CASCADE
);

-- Model specification: every hash is a distinct embedding "run"
CREATE TABLE IF NOT EXISTS embedding_specs (
  spec_hash      TEXT PRIMARY KEY,
  model_name     TEXT NOT NULL,
  modality       TEXT NOT NULL,          -- 'audio' | 'text'
  dim            INTEGER NOT NULL,
  spec_json      TEXT NOT NULL,          -- full json() from Embedder.spec()
  code_version   TEXT,                   -- music_data.__version__ or git commit
  lib_versions   TEXT,                   -- e.g. "numpy==2.0;torch==2.1"
  created_at     TEXT DEFAULT (datetime('now'))
);

-- Raw embedding outputs (per-window for audio, single for text)
-- Primary key = skip key for idempotency
CREATE TABLE IF NOT EXISTS embeddings (
  spec_hash      TEXT NOT NULL REFERENCES embedding_specs(spec_hash),
  input_hash     TEXT NOT NULL,          -- audio_sha256 or sha256(text blob)
  entity_type    TEXT NOT NULL,          -- 'track' | 'artist'
  entity_id      TEXT,                  -- recording.id (track) or artists.mbid (artist)
  n_windows      INTEGER DEFAULT 1,
  vectors        BLOB NOT NULL,          -- numpy float32, shape (n, dim) flattened
  created_at     TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (spec_hash, input_hash)
);

-- Derived artist-level vectors (cheap, safe to drop & rebuild)
CREATE TABLE IF NOT EXISTS artist_vectors (
  spec_hash      TEXT NOT NULL REFERENCES embedding_specs(spec_hash),
  mbid           TEXT NOT NULL,          -- artists.mbid
  pooling        TEXT NOT NULL,          -- 'mean' | 'sum' | 'concat'
  n_tracks       INTEGER,
  source_inputs  TEXT,                  -- JSON list of input_hashes used
  vector         BLOB NOT NULL,          -- float32, shape (dim,) or (n*dim,)
  PRIMARY KEY (spec_hash, mbid, pooling)
);

-- Indexes for fast lookup / skipping
CREATE INDEX IF NOT EXISTS idx_embeddings_input ON embeddings(spec_hash, input_hash);
CREATE INDEX IF NOT EXISTS idx_audio_clips_track ON audio_clips(track_id);
CREATE INDEX IF NOT EXISTS idx_artist_vectors_mbid ON artist_vectors(mbid);
