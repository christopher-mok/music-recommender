-- ============================================================
-- music_data - Initial schema (v2)
-- SQLite 3.35+
--
-- Design notes:
--  * Artists and recordings use internal integer IDs so entities
--    without a MusicBrainz ID (e.g. provider-only stub artists)
--    can still be stored.
--  * MusicBrainz IDs are nullable unique external identifiers.
--  * Similarities and popularity measurements are provider-scoped.
--  * All collection writes are idempotent through UNIQUE constraints.
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    migration_file TEXT UNIQUE NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Providers / data sources and their licensing notes.
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    base_url TEXT,
    license TEXT,
    notes TEXT
);

-- One row per CLI collection invocation.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'partial', 'failed', 'cancelled')),
    config_json TEXT,
    stats_json TEXT,
    error TEXT
);

-- Per-entity work items, used for progress tracking and reporting.
CREATE TABLE IF NOT EXISTS ingestion_job_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL CHECK (item_type IN ('artist', 'recording', 'similarity')),
    identifier TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'in_progress', 'success', 'failed', 'skipped', 'ambiguous')),
    attempt INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (job_id, item_type, identifier)
);

-- ============================================================
-- Artists
-- ============================================================
CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid TEXT UNIQUE,
    name TEXT NOT NULL,
    sort_name TEXT,
    disambiguation TEXT,
    artist_type TEXT,
    country TEXT,
    area_id TEXT,
    area_name TEXT,
    begin_year INTEGER,
    end_year INTEGER,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    -- 1 when we have collected full MusicBrainz metadata.
    is_canonical INTEGER NOT NULL DEFAULT 0 CHECK (is_canonical IN (0, 1)),
    collection_status TEXT NOT NULL DEFAULT 'stub'
        CHECK (collection_status IN ('stub', 'partial', 'complete', 'failed')),
    first_collected TEXT NOT NULL DEFAULT (datetime('now')),
    last_collected TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS artist_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    sort_name TEXT,
    begin_year INTEGER,
    end_year INTEGER,
    alias_type TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'musicbrainz',
    UNIQUE (artist_id, name, alias_type, language, source)
);

-- Normalized tag vocabulary. tag_type separates genres from
-- descriptive tags so genre coverage can be measured accurately.
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    tag_type TEXT NOT NULL DEFAULT 'descriptive'
        CHECK (tag_type IN ('genre', 'descriptive', 'mood', 'instrument', 'other')),
    UNIQUE (normalized_name, tag_type)
);

CREATE TABLE IF NOT EXISTS artist_tags (
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    count INTEGER,
    weight REAL,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (artist_id, tag_id, source)
);

-- ============================================================
-- Releases and recordings
-- ============================================================
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid TEXT UNIQUE,
    title TEXT NOT NULL,
    release_group_mbid TEXT,
    release_date TEXT,
    country TEXT,
    primary_type TEXT,
    secondary_type TEXT,
    source TEXT NOT NULL DEFAULT 'musicbrainz',
    first_collected TEXT NOT NULL DEFAULT (datetime('now')),
    last_collected TEXT
);

CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid TEXT UNIQUE,
    title TEXT NOT NULL,
    disambiguation TEXT,
    duration_ms INTEGER,
    first_release_date TEXT,
    is_canonical INTEGER NOT NULL DEFAULT 0 CHECK (is_canonical IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'musicbrainz',
    first_collected TEXT NOT NULL DEFAULT (datetime('now')),
    last_collected TEXT
);

CREATE TABLE IF NOT EXISTS recording_artists (
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    credit_order INTEGER NOT NULL DEFAULT 0,
    credit_name TEXT,
    join_phrase TEXT,
    PRIMARY KEY (recording_id, artist_id, credit_order)
);

CREATE TABLE IF NOT EXISTS recording_releases (
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
    disc_number INTEGER,
    track_number INTEGER,
    PRIMARY KEY (recording_id, release_id)
);

CREATE TABLE IF NOT EXISTS recording_tags (
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    count INTEGER,
    weight REAL,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (recording_id, tag_id, source)
);

-- ============================================================
-- External identifiers (provider-scoped, never conflated with MBIDs)
-- ============================================================
CREATE TABLE IF NOT EXISTS artist_external_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (artist_id, provider, external_id)
);

CREATE TABLE IF NOT EXISTS recording_external_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (recording_id, provider, external_id)
);

-- ============================================================
-- Similarity relationships
-- ============================================================
CREATE TABLE IF NOT EXISTS artist_similarities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    target_artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    score REAL,
    raw_score TEXT,
    score_description TEXT,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_artist_id, target_artist_id, provider),
    CHECK (source_artist_id <> target_artist_id)
);

-- ============================================================
-- Popularity measurements (provider scoped, timestamped)
-- ============================================================
CREATE TABLE IF NOT EXISTS artist_popularity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    measure TEXT NOT NULL,
    value REAL,
    rank INTEGER,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (artist_id, provider, measure)
);

CREATE TABLE IF NOT EXISTS recording_popularity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    measure TEXT NOT NULL,
    value REAL,
    rank INTEGER,
    retrieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (recording_id, provider, measure)
);

-- ============================================================
-- Provenance / raw response cache metadata
-- ============================================================
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    method TEXT NOT NULL,
    url TEXT NOT NULL,
    status_code INTEGER,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    from_cache INTEGER NOT NULL DEFAULT 0 CHECK (from_cache IN (0, 1)),
    cache_path TEXT,
    response_hash TEXT
);

-- Why each artist was selected for collection.
CREATE TABLE IF NOT EXISTS seed_selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES ingestion_jobs(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    mbid TEXT,
    reason TEXT NOT NULL,
    genre TEXT,
    country TEXT,
    selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (job_id, name)
);

-- ============================================================
-- Indexes
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_artists_name ON artists(name);
CREATE INDEX IF NOT EXISTS idx_artists_country ON artists(country);
CREATE INDEX IF NOT EXISTS idx_artists_status ON artists(collection_status);
CREATE INDEX IF NOT EXISTS idx_artists_canonical ON artists(is_canonical);
CREATE INDEX IF NOT EXISTS idx_artist_aliases_artist ON artist_aliases(artist_id);
CREATE INDEX IF NOT EXISTS idx_artist_tags_artist ON artist_tags(artist_id);
CREATE INDEX IF NOT EXISTS idx_artist_tags_tag ON artist_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_recordings_title ON recordings(title);
CREATE INDEX IF NOT EXISTS idx_recordings_mbid ON recordings(mbid);
CREATE INDEX IF NOT EXISTS idx_recording_artists_artist ON recording_artists(artist_id);
CREATE INDEX IF NOT EXISTS idx_recording_artists_recording ON recording_artists(recording_id);
CREATE INDEX IF NOT EXISTS idx_recording_releases_release ON recording_releases(release_id);
CREATE INDEX IF NOT EXISTS idx_recording_tags_recording ON recording_tags(recording_id);
CREATE INDEX IF NOT EXISTS idx_similarities_source ON artist_similarities(source_artist_id);
CREATE INDEX IF NOT EXISTS idx_similarities_target ON artist_similarities(target_artist_id);
CREATE INDEX IF NOT EXISTS idx_similarities_provider ON artist_similarities(provider);
CREATE INDEX IF NOT EXISTS idx_artist_popularity_artist ON artist_popularity(artist_id);
CREATE INDEX IF NOT EXISTS idx_recording_popularity_recording ON recording_popularity(recording_id);
CREATE INDEX IF NOT EXISTS idx_fetch_log_provider_time ON fetch_log(provider, fetched_at);
CREATE INDEX IF NOT EXISTS idx_job_items_job ON ingestion_job_items(job_id);
CREATE INDEX IF NOT EXISTS idx_job_items_status ON ingestion_job_items(status);
