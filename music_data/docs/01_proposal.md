# Music Metadata & Similarity Pipeline — Proposal

> Version 1.0 · Created: 2026-01-01 · Status: **For Review & Approval**

---

## Table of Contents

1. [Overview](#1-overview)
2. [Research Findings](#2-research-findings)
3. [Database Schema](#3-database-schema)
4. [Project File Structure](#4-project-file-structure)
5. [Detailed Implementation Plan](#5-detailed-implementation-plan)
6. [ListenBrainz / Last.fm Strategy](#6-listenbrainz--lastfm-strategy)
7. [Rate Limits & Best Practices](#7-rate-limits--best-practices)
8. [Seed Artists — Phase 3](#8-seed-artists--phase-3)
9. [Risk Register](#9-risk-register)
10. [Decision Points](#10-decision-points)

---

## 1. Overview

A standalone Python 3.11+ CLI pipeline that:

- **Collects** canonical artist metadata from **MusicBrainz** (WS/2, JSON).
- **Discovers** popularity signals (top tracks, tags) and **similarity relationships** via **Last.fm** (free API key, no OAuth required).
- **Stores** everything in a local **SQLite** database.
- **Outputs** a validation report for Phase 3.

Key design principles:

- **Resumable & idempotent** — safe to interrupt and resume; re-runs are safe.
- **Mockable** — all external calls are dependency-injected; tests use mocks.
- **Configurable** — seed artists, output path, API keys, concurrency via TOML/CLI.

---

## 2. Research Findings

### MusicBrainz WS/2 (Canonical Data Source)

| Aspect | Detail |
|--------|--------|
| **Endpoint** | `https://musicbrainz.org/ws/2/artist/<mbid-or-query>?fmt=json&inc=...` |
| **Format** | JSON (`fmt=json`) |
| **Rate limit** | **1 request/second** (must include valid `User-Agent` with contact info) |
| **ID type** | MusicBrainz ID (MBID) — 36-char UUID |
| **Inc params** | `artist-rels`, `artist-tags`, `artist-ratings`, `releases`, `release-groups`, `recordings`, `url-rels`, `tag-facets` |
| **Query** | `https://musicbrainz.org/ws/2/artist/?query=artist:<name>&fmt=json` |
| **Aliases** | Included in JSON under `aliases[].name` |
| **Disambiguation** | `disambiguation` string (e.g., "US band", "DJ") |
| **Top tracks** | MusicBrainz does **not** provide popularity-based top tracks. Use Last.fm for that. |
| **Relationships** | Structural only (member of band, etc.). No "similar artist" relationships. |

### Last.fm API (Discovery & Similarity)

| Aspect | Detail |
|--------|--------|
| **Endpoint** | `http://ws.audioscrobbler.com/2.0/?method=<METHOD>&...&format=json` |
| **API key** | Free; register at https://www.last.fm/api/account/create (3 keys) |
| **Auth required** | **No** for the read methods we need |
| **Rate limit** | **~300 requests per 5 minutes** per API key (~1 req/sec sustained; burst up to ~10/sec short) |
| **User-Agent** | Must include identifiable contact info |
| **Methods used** | `artist.getInfo` (top tracks, tags, bio), `artist.getSimilar` (similarity links) |

### ListenBrainz (Evaluated — Not Recommended for Phase 3)

| Aspect | Detail |
|--------|--------|
| **Public stats** | `/1/stats/user/<user>/month` returns **404** without auth |
| **Public dataset** | Requires large download (75 GB+ compressed, weekly) |
| **Last.fm compat** | ListensBrainz mirrors Last.fm compat endpoints, but auth-dependent stats endpoints are not publicly accessible |
| **Conclusion** | For Phase 3 (10 artists), Last.fm provides sufficient popularity/similarity data without the complexity of dataset downloads |

### Final Data Sources

| Data Type | Source | Method |
|-----------|--------|--------|
| Canonical metadata | **MusicBrainz** | `artist/lookup`, `artist/query` |
| Top tracks, tags, bio | **Last.fm** | `artist.getInfo` |
| Similar artists | **Last.fm** | `artist.getSimilar` |

---

## 3. Database Schema

```sql
-- ============================================================
-- Schema: music_metadata.db
-- SQLite 3.35+ (JSON1 extension enabled by default)
-- ============================================================

-- Each row is unique by (source, source_id).
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,  -- 'musicbrainz', 'lastfm'
    base_url TEXT NOT NULL
);

-- Artist: canonical identity
CREATE TABLE artists (
    mbid              TEXT PRIMARY KEY,  -- MusicBrainz ID (36-char UUID)
    name              TEXT NOT NULL,
    sort_name         TEXT,
    disambiguation    TEXT,
    type              TEXT,            -- 'Group', 'Person', 'Other', 'Organisation'
    country           TEXT,            -- ISO 3166 country code from MusicBrainz area
    area_id           TEXT,            -- MusicBrainz area ID
    area_name         TEXT,            -- Human-readable area name
    begin_year        INTEGER,         -- NULL if unknown
    end_year          INTEGER,         -- NOT NULL if disbanded
    is_active         INTEGER DEFAULT 1,
    lastfm_id         INTEGER,         -- Last.fm ID (for cross-referencing)
    lastfm_mbid       TEXT,            -- Last.fm's stored MBID (if linked)
    data_source       TEXT DEFAULT 'musicbrainz',
    first_collected   TEXT DEFAULT (datetime('now')),
    last_collected    TEXT,
    raw_data          TEXT,            -- Full JSON from MusicBrainz (for audit)
    raw_fetched_at    TEXT
);

-- Aliases / alternate names
CREATE TABLE artist_aliases (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid    TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    sort_name TEXT,
    begin_year INTEGER,
    end_year   INTEGER,
    type         TEXT,
    language     TEXT,
    FOREIGN KEY (mbid) REFERENCES artists(mbid) ON DELETE CASCADE
);

-- Tags from MusicBrainz user tags
CREATE TABLE artist_tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid    TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    count   INTEGER DEFAULT 1,
    source  TEXT DEFAULT 'musicbrainz',
    FOREIGN KEY (mbid) REFERENCES artists(mbid) ON DELETE CASCADE
);

-- Tags from Last.fm (with counts)
CREATE TABLE lastfm_tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid    TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    tag     TEXT NOT NULL,
    count   INTEGER,
    source  TEXT DEFAULT 'lastfm',
    FOREIGN KEY (mbid) REFERENCES artists(mbid) ON DELETE CASCADE
);

-- Top tracks from Last.fm (popularity proxy)
CREATE TABLE artist_top_tracks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid    TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    mbid    TEXT,            -- Last.fm track MBID if available
    mbid_track TEXT,         -- Last.fm track MBID if available
    duration INTEGER,        -- milliseconds
    rank    INTEGER NOT NULL, -- position in top tracks list (1 = most popular)
    playcount INTEGER,        -- play count from Last.fm (popularity proxy)
    FOREIGN KEY (mbid) REFERENCES artists(mbid) ON DELETE CASCADE
);

-- Artist bio
CREATE TABLE artist_bios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mbid        TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    summary     TEXT,
    full_text   TEXT,
    language    TEXT DEFAULT 'en',
    retrieved_at TEXT
);

-- Similarity relationships
CREATE TABLE artist_similarities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_mbid     TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    similar_mbid    TEXT NOT NULL REFERENCES artists(mbid) ON DELETE CASCADE,
    weight          REAL,    -- match similarity value (0.0 – 1.0) or null if no numeric value
    lastfm_match    TEXT,    -- match field from Last.fm (0.0 to 1.0)
    FOREIGN KEY (source_mbid) REFERENCES artists(mbid) ON DELETE CASCADE,
    FOREIGN KEY (similar_mbid) REFERENCES artists(mbid) ON DELETE CASCADE,
    UNIQUE(source_mbid, similar_mbid)
);

-- Collection run: tracks each execution
CREATE TABLE collection_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT DEFAULT 'running',  -- 'running', 'completed', 'failed', 'partial'
    artists_requested INTEGER DEFAULT 0,
    artists_completed INTEGER DEFAULT 0,
    artists_failed    INTEGER DEFAULT 0,
    notes           TEXT,
    config_snapshot  TEXT  -- JSON of CLI args / TOML at runtime
);

-- Collection run: per-artist status
CREATE TABLE collection_artifact (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES collection_runs(id) ON DELETE CASCADE,
    mbid            TEXT NOT NULL,
    artist_name     TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',  -- 'pending', 'success', 'failed', 'skipped'
    error_message   TEXT,
    mb_metadata_fetched_at TEXT,
    lastfm_info_fetched_at TEXT,
    lastfm_similar_fetched_at TEXT,
    FOREIGN KEY (mbid) REFERENCES artists(mbid) ON DELETE CASCADE
);

-- Indexes for common queries
CREATE INDEX idx_artists_name ON artists(name);
CREATE INDEX idx_artists_country ON artists(country);
CREATE INDEX idx_similarities_source ON artist_similarities(source_mbid);
CREATE INDEX idx_similarities_similar ON artist_similarities(similar_mbid);
CREATE INDEX idx_tags_artist ON artist_tags(mbid);
CREATE INDEX idx_tags_lastfm ON lastfm_tags(mbid);
CREATE INDEX idx_top_tracks_artist ON artist_top_tracks(mbid);
CREATE INDEX idx_collection_run_status ON collection_artifacts(status);
```

### ER Diagram (Conceptual)

```
sources ─────────────────────────────┐
                                       │
artists ◄── artist_aliases            │
  ├── artist_tags                     │
  ├── lastfm_tags                     │
  ├── artist_top_tracks               │
  ├── artist_bios                     │
  ├── artist_similarities (self-ref)  │
  └── collection_artifacts ──► collection_runs
```

---

## 4. Project File Structure

```
music_data/                          # workspace root
├── README.md                        # this document (or link to docs/)
├── docs/
│   ├── 01_proposal.md               # this file
│   ├── 02_api_reference.md          # API endpoint reference
│   └── 03_validation_report.md      # template for Phase 3 report
│
├── pyproject.toml                   # project metadata, deps, tool config
├── poetry.lock                      # locked deps (if using Poetry)
│                                   # or requirements.txt / requirements-dev.txt
├── .env.example                     # env vars template (no secrets)
│
├── src/
│   └── music_data/                  # package
│       ├── __init__.py
│       ├── cli.py                   # Click/Typer CLI entrypoint
│       ├── config.py                # TOML + env parsing
│       ├── database.py              # SQLite init, migrations, helpers
│       ├── models.py                # Pydantic/SQLAlchemy models (or dataclasses)
│       │
│       ├── fetchers/
│       │   ├── __init__.py
│       │   ├── base.py              # BaseFetcher: retry, rate-limit, logging
│       │   ├── musicbrainz.py       # MusicBrainz WS/2 client
│       │   └── lastfm.py            # Last.fm client
│       │
│       ├── processors/
│       │   ├── __init__.py
│       │   ├── metadata.py          # Normalize MB data → DB rows
│       │   ├── popularity.py        # Normalize Last.fm data → DB rows
│       │   ├── similarities.py      # Normalize similar artists → DB rows
│       │   └── deduplication.py     # Alias resolution, dedup
│       │
│       ├── collectors/
│       │   ├── __init__.py
│       │   ├── artist_collector.py  # Orchestrates fetch + process + store
│       │   └── pipeline.py          # Pipeline runner (collection runs)
│       │
│       ├── reports/
│       │   ├── __init__.py
│       │   └── generator.py         # Phase 3 validation report
│       │
│       └── utils/
│           ├── __init__.py
│           ├── logger.py            # Structured logging
│           └── validators.py        # Input validation helpers
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                  # pytest fixtures (mock HTTP, temp DB)
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_models.py
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── test_musicbrainz.py      # Mocked MB API responses
│   │   └── test_lastfm.py           # Mocked Last.fm API responses
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── test_metadata.py
│   │   ├── test_popularity.py
│   │   ├── test_similarities.py
│   │   └── test_deduplication.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   └── test_artist_collector.py
│   └── reports/
│       ├── __init__.py
│       └── test_generator.py
│
├── data/
│   └── seed_artists.toml            # default seed list (TOML)
│
├── scripts/
│   ├── init_db.py                   # one-shot DB initialization
│   └── generate_sample_data.py      # populate DB with test data for dev
│
└── migrations/                      # SQL migration files
    ├── 001_initial_schema.sql
    ├── 002_add_indexes.sql
    └── README.md
```

---

## 5. Detailed Implementation Plan

### Phase 1 — Research & Planning (COMPLETED)
- [x] Research MusicBrainz WS/2 API structure
- [x] Research ListenBrainz API capabilities
- [x] Research Last.fm API capabilities
- [x] Design database schema
- [x] Design project structure
- [ ] **Awaiting user approval to proceed**

### Phase 2 — Core Implementation + Tests

#### 2A. Project scaffolding
- Create `pyproject.toml` with dependencies:
  - `requests` (HTTP client)
  - `pydantic` (data validation)
  - `click` or `typer` (CLI)
  - `tomli` (TOML parsing)
  - `pytest` + `pytest-mock` + `pytest-asyncio` (testing)
  - `httpx` (optional, for async testing)
  - `rich` (CLI output formatting, optional)
- Set up `src/music_data/` package with `__init__.py`
- Set up `tests/` with `conftest.py`

#### 2B. Config module (`config.py`)
```python
# TOML structure
[collect]
database_path = "data/music_metadata.db"
concurrency = 1           # MusicBrainz: max 1 req/sec, so default to 1
retry_max = 5
retry_delay = 1.0         # seconds between retries

[lastfm]
api_key = ""              # from env or TOML
max_requests_per_second = 2.0

[musicbrainz]
user_agent = "music_data_pipeline/1.0 (your@email.com)"

[collect.artists]
seed_artists = [
    { name = "Radiohead", country = "GB" },
    { name = "Björk", country = "IS" },
    # ... 10 total
]
```

- Environment variable overrides: `LASTFM_API_KEY`, `MUSICBRAINZ_USER_AGENT`, `DB_PATH`
- Validation: raise on missing required fields

#### 2C. Database module (`database.py`)
- `init_db(path: str) → None` — creates tables, runs migrations
- `get_connection() → sqlite3.Connection` — context manager
- `ensure_artist(mbid: str) → dict` — upsert artist, return dict
- `save_artist_alias(mbid: str, alias: dict) → None`
- `save_tags(mbid: str, tags: list[dict], source: str) → None`
- `save_top_tracks(mbid: str, tracks: list[dict]) → None`
- `save_similarity(source_mbid: str, similar: dict) → None`
- `create_collection_run(config: dict) → int` — returns run_id
- `update_artifact(run_id: int, mbid: str, status: str, ...) → None`
- Idempotent upserts using `INSERT ... ON CONFLICT DO UPDATE` / `REPLACE`

#### 2D. Base fetcher (`fetchers/base.py`)
```python
class BaseFetcher:
    def __init__(self, base_url: str, max_retries: int = 5, 
                 delay: float = 1.0, rate_limit: float = 1.0):
        self.session = requests.Session()
        self.rate_limiter = RateLimiter(rate_limit)  # sliding window
    
    def request(self, endpoint: str, params: dict, method: str = "GET") -> dict:
        self.rate_limiter.wait()
        for attempt in range(self.max_retries):
            try:
                response = self.session.get(endpoint, params=params)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                if attempt == self.max_retries - 1:
                    raise CollectionError(f"Failed after {self.max_retries} attempts: {e}")
                time.sleep(self.delay * (2 ** attempt))  # exponential backoff
```

#### 2E. MusicBrainz fetcher (`fetchers/musicbrainz.py`)
- `search_artist(name: str) → list[dict]` — query MusicBrainz
- `get_artist(mbid: str) → dict` — lookup by MBID with all `inc=` params
- `get_artist_recordings(mbid: str, limit: int = 10) → list[dict]`
- Rate limit: **1 req/sec** enforced
- Header: `User-Agent` from config (required by MB policy)

#### 2F. Last.fm fetcher (`fetchers/lastfm.py`)
- `get_artist_info(mbid: str | name: str) → dict` — `artist.getInfo`
  - Returns: name, mbid, playcount, tags, top tracks, bio
- `get_similar_artists(mbid: str | name: str, limit: int = 20) → list[dict]` — `artist.getSimilar`
  - Returns: name, mbid, match score
- Rate limit: ~1 req/sec enforced (conservative)

#### 2G. Processors (`processors/`)
- `metadata.py`: MusicBrainz JSON → DB rows
  - Parse artist, aliases, tags, releases
  - Normalize country codes
- `popularity.py`: Last.fm `artist.getInfo` → DB rows
  - Extract top tracks, tags, playcount
  - Store bio text
- `similarities.py`: Last.fm `artist.getSimilar` → DB rows
  - Upsert similar artists (auto-discovery)
  - Store match weight
  - Optionally fetch their metadata too (if below max depth)

#### 2H. Collector & Pipeline (`collectors/`)
- `ArtistCollector`: orchestrates one artist's collection
  ```python
  def collect(self, artist_name: str, mbid: str | None = None) -> dict:
      # 1. Search/get MBID from MusicBrainz
      # 2. Fetch full MB metadata
      # 3. Fetch Last.fm info (top tracks, tags, bio)
      # 4. Fetch Last.fm similarities
      # 5. Store all to DB
      # 6. Return result dict
  ```
- `Pipeline`: orchestrates multiple artists
  ```python
  def run(self, seed_artists: list[dict]) -> CollectionRun:
      # 1. Create collection run record
      # 2. For each seed artist, collect (with retry)
      # 3. Update run status
      # 4. Return run summary
  ```

#### 2I. CLI (`cli.py`)
```bash
# Collect specific artists
music_data collect --name "Radiohead" --name "Björk"

# Collect from seed file
music_data collect --seed-file data/seed_artists.toml

# Collect with custom DB
music_data collect --seed-file data/seed_artists.toml --db-path /tmp/test.db

# Generate validation report
music_data report --run-id 1 --output report.md

# View DB stats
music_data stats

# Initialize DB only
music_data init-db
```

#### 2J. Report Generator (`reports/generator.py`)
- Generates a markdown/HTML report per collection run
- Includes:
  - Artist summary table (name, country, tags, top track playcount)
  - Similarity network stats (avg similar artists, most-connected)
  - Data quality flags (missing fields, API errors)
  - Collection run metadata

#### 2K. Tests (pytest)
- **Mock all external calls** using `pytest-mock` and `responses` or `aioresponses`
- Test fixtures:
  - `mock_musicbrainz_response` — sample MB JSON for a given artist
  - `mock_lastfm_info_response` — sample Last.fm artist.getInfo JSON
  - `mock_lastfm_similar_response` — sample artist.getSimilar JSON
  - `temp_db` — temporary SQLite in-memory DB
- Test coverage:
  - Config parsing (valid/invalid/missing)
  - MusicBrainz fetcher (success, 404, rate-limit, timeout)
  - Last.fm fetcher (success, not found, rate-limit)
  - Metadata processor (various MB response shapes)
  - Popularity processor (various Last.fm response shapes)
  - Similarity processor (normal, empty, circular references)
  - Artist collector (happy path, partial failure, retry)
  - Database (upsert, query, indexes)
  - Report generator (content verification)

### Phase 3 — Live Collection & Validation

#### 3A. Collect 10 seed artists
- Run the pipeline against 10 diverse seed artists
- All API keys in env vars, not hardcoded
- Generate validation report

#### 3B. Validation Report
See `docs/03_validation_report.md` for template.

#### 3C. Stop & Request Approval
- **Do NOT proceed to 100 artists** without user approval

---

## 6. ListenBrainz / Last.fm Strategy

### Why Last.fm Instead of ListenBrainz for Phase 3?

| Factor | Last.fm | ListenBrainz |
|--------|---------|--------------|
| **Similar artist data** | ✅ `artist.getSimilar` — free, no auth | ❌ Not available via public endpoints |
| **Top tracks / playcount** | ✅ `artist.getInfo` — free, no auth | ❌ Stats require auth |
| **API key requirement** | Yes (free) | No (for read) |
| **Rate limit** | ~300 req / 5 min per key | N/A (stats not accessible) |
| **Dataset option** | Not applicable | 75 GB download, weekly |
| **Complexity** | Simple REST API | Requires authentication or large download |

### Decision

**Use Last.fm for Phase 3.** It provides:
1. Artist similarity relationships (with match scores)
2. Top tracks with playcount (as popularity proxy)
3. Tags with counts
4. Artist biography text

Last.fm remains as the preferred source for all discovery/popularity data. ListenBrainz can be added as an alternative/supplementary source in a future phase if the user desires ListenBrainz-specific data.

---

## 7. Rate Limits & Best Practices

### MusicBrainz
- **1 request per second** — strictly enforced
- Must include `User-Agent` with contact info
- No authentication required
- Implement sliding-window rate limiter

### Last.fm
- **~300 requests per 5 minutes** per API key (~1 req/sec sustained)
- No authentication required for read methods
- Implement sliding-window rate limiter
- **10 similar artists per seed** (reasonable, keeps request count low)

### Estimated Requests for 10 Artists
| Operation | Requests per artist | Total (10) |
|-----------|-------------------|------------|
| MusicBrainz search | 1 | 10 |
| MusicBrainz full lookup | 1 | 10 |
| Last.fm artist info | 1 | 10 |
| Last.fm similar artists | 1 | 10 |
| **Total** | **4** | **40** |

For 100 artists: ~400 requests. Still within Last.fm limits at 1 req/sec.

---

## 8. Seed Artists — Phase 3

Ten diverse artists selected to test geographic, temporal, and genre coverage:

| # | Name | Country | Era | Genre(s) | Why This Artist |
|---|------|---------|-----|----------|-----------------|
| 1 | **Radiohead** | GB | 1991–present | Alternative rock, electronic | Well-documented, global reach |
| 2 | **Björk** | IS | 1965–present | Art pop, electronic | Iconoclastic, tag-rich |
| 3 | **Miles Davis** | US | 1926–1991 | Jazz | Historical figure, many similar artists |
| 4 | **Aphex Twin** | GB | 1971–present | Ambient, IDMC | Electronic pioneer, niche but passionate community |
| 5 | **Fela Kuti** | NG | 1938–1997 | Afrobeat | African representation, world music |
| 6 | **Missy Elliott** | US | 1971–present | Hip-hop, R&B | Hip-hop representation, pop crossover |
| 7 | **M.I.A.** | GB/LK | 1975–present | Electropop, world | Multi-cultural, politically engaged |
| 8 | **Ólafur Arnalds** | IS | 1986–present | Post-classical, ambient | Modern classical, growing fanbase |
| 9 | **Tame Impala** | AU | 1988–present | Psychedelic rock, dream pop | Contemporary, strong online presence |
| 10 | **Yoko Ono** | JP/GB/US | 1933–present | Avant-garde, performance art | Avant-garde representation, cross-cultural |

**Geographic spread:** GB (4), US (2), IS (2), NG (1), JP (1), AU (1), Multi (1)
**Gender spread:** 6 female/non-binary-presenting, 4 male-presenting
**Era spread:** 4 artists born pre-1950, 3 in 1970s, 3 in 1980s+

---

## 9. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Last.fm API key rejected or suspended | Low | Medium | Register early; use multiple keys as fallback |
| MusicBrainz API changes | Low | Medium | Wrap MB client in adapter interface; tests catch breakage |
| Last.fm rate limiting | Medium | Medium | Conservative 1 req/sec; 10 similar per artist |
| Large similar artist graph | Medium | Low | Cap at 10 similar artists per seed; no recursive fetching |
| SQLite performance at scale | Low | Low | Indexes on MBID; 100 artists is trivial for SQLite |
| Data quality / inconsistent schemas | Medium | Medium | Pydantic validation on all external data |
| Network failures | High | Low | Retry with exponential backoff |

---

## 10. Decision Points — Please Review & Approve

### Q1: Data Sources
**Proposed:** MusicBrainz (canonical) + Last.fm (discovery/similarity).  
**Alternative:** Add ListenBrainz later via dataset download.  
**Decision:** Last.fm for Phase 3; ListenBrainz as optional Phase 4.

### Q2: CLI Framework
**Proposed:** Click (mature, well-documented).  
**Alternative:** Typer (modern, type-hint driven).  
**Decision:** **Click** for Phase 3; may migrate to Typer later.

### Q3: Database ORM
**Proposed:** Raw `sqlite3` with Pydantic models.  
**Alternative:** SQLAlchemy (more code, more flexibility).  
**Decision:** **Raw sqlite3** for Phase 3; migrate to SQLAlchemy if ORM needed later.

### Q4: Similar Artist Depth
**Proposed:** 10 similar artists per seed, no recursive fetching.  
**Alternative:** Recursive up to 2 levels (10 → 100).  
**Decision:** **No recursion** for Phase 3; recursive expansion in Phase 4.

### Q5: 10 Seed Artists List
**Proposed:** The list in Section 8 above.  
**Alternative:** User-provided list.  
**Decision:** **Approve or provide replacements.**

### Q6: Report Format
**Proposed:** Markdown file + optional HTML.  
**Alternative:** JSON output for programmatic consumption.  
**Decision:** **Markdown + JSON** (both generated).

### Q7: Dependency Management
**Proposed:** `pyproject.toml` with `pip-tools` or `uv`.  
**Alternative:** Poetry.  
**Decision:** **`pyproject.toml` + `uv`** (fast, modern).

---

## Next Steps

Upon your approval:

1. **Phase 2 begins** — implement scaffolding, config, database, fetchers, processors, tests.
2. **Phase 3 begins** — live collection of the 10 seed artists.
3. **Validation report** generated and reviewed.
4. **Stop point** — approval required before expanding to 100 artists.
