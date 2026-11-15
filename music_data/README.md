# Local Music Data Pipeline

A Python 3.11+ CLI that collects canonical artist metadata, a small list of
popular recordings, provider-scoped similarity links, and provenance into
SQLite. It does not download audio or build a recommender.

## Setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Set `MUSICBRAINZ_USER_AGENT` to an identifiable application name and contact
address before making live MusicBrainz requests. The default is deliberately
not a usable production contact. `MUSIC_DATA_DB` overrides the configured DB
path. API responses are cached under `.cache/music_data` by default; set
`MUSIC_DATA_USE_CACHE=0` or use `--no-cache` to disable it.

## Commands

```bash
python -m music_data init
python -m music_data collect --limit 10 --dry-run
python -m music_data collect --limit 10
python -m music_data validate
python -m music_data report --format json --output report.json
```

Collect one artist, optionally with an explicit MBID to avoid ambiguous name
resolution:

```bash
python -m music_data collect --artist "Radiohead" --country GB
python -m music_data collect --artist "Radiohead" --mbid a74b1b7f-71a5-4011-9441-d0b5e4122711
python -m music_data collect --resume
```

Add current popular artists from ListenBrainz sitewide stats (seeds come with
MBIDs, are deduped against configured seeds, and multi-artist credits are skipped):

```bash
python -m music_data collect --popular 200 --popular-range this_year
python -m music_data collect --popular 200 --popular-only --dry-run
```

Seed configuration lives in `data/seed_artists.toml`. Use `--config PATH`,
`--db-path PATH`, `--tracks-per-artist N`, `--similar-limit N`, and
`--no-listenbrainz` to override settings. `collect --resume` resumes the latest
incomplete collection job, skipping its successful artist items. Artists are
committed independently, so interruption does not roll back prior artists.

## Sources and limitations

* **MusicBrainz WS/2** is the canonical source for identities, aliases, country,
  types, tags/genres, recording/release metadata. Requests are rate-limited to
  one per second. Artist-name resolution requires an exact unique match after
  optional country filtering; ambiguous matches fail instead of being merged.
* **ListenBrainz** supplies similar-artist relationships through the Dataset
  Hoster `similar-artists` endpoint. Similarity scores are retained as
  ListenBrainz scores and are not comparable to another provider's scores.
  The `top-recordings-for-artist` API now returns HTTP 401 without an auth
  token. If `LISTENBRAINZ_USER_TOKEN` is set, the pipeline uses it. Otherwise
  it falls back to a MusicBrainz `arid:` recording search, filters obvious
  interviews/segues/alternate versions, and enriches the selected recordings
  with release metadata. This fallback is relevance-ranked, not popularity-
  ranked, and is labeled as such in the report data. Sitewide popular artists
  are available for discovery, but this initial collector uses configured
  seeds and does not automatically expand the catalog.
* **Last.fm** is disabled and not used by collection. Its caching,
  research, and redistribution terms need review before adding any persisted
  Last.fm data to an offline-shipped database.
* Response caches contain raw JSON, including provider data. Review each source's
  current terms and licenses before distributing the cache or derived data.
* Popular recording selection uses ListenBrainz's current top-recordings API;
  it is a listener-popularity proxy, not an editorial “representative track”
  judgment. MusicBrainz recording lookups enrich the selected IDs. If a lookup
  fails, the ListenBrainz recording remains with its stable MBID and title.

## Popularity, embeddings and the API bundle

```bash
python -m music_data collect-popularity                 # ListenBrainz listener counts
python -m music_data publish --output-dir api_bundle    # read-only bundle for other modules
```

Other backend modules should not query the pipeline DB. They load the bundle
through `music_data.api` (Python) or the TypeScript port in `ts/`. See
[docs/API.md](docs/API.md). For the raw schema and embedding runs, see
[docs/USING_THE_DATA.md](docs/USING_THE_DATA.md).

The visualizer runs with `streamlit run scripts/viz.py` (`pip install -e ".[viz]"`);
on Windows use `scripts\windows\run_viz.bat`.

## Database

The schema migration is packaged under `src/music_data/migrations/` and is
applied by `init`. Artists and recordings have internal integer IDs, with
MusicBrainz IDs stored as nullable unique identifiers. Supporting tables store
aliases, typed tags, releases, ordered recording credits, external IDs,
provider-scoped popularity, similarities, seed-selection reasons, fetch
provenance, and ingestion job progress. Similar artists outside the seed list
are kept as non-canonical artist stubs; they are not discarded or mistaken for
fully collected canonical artists.

## Tests

```bash
python -m pytest
```

Tests use mocked HTTP responses and temporary SQLite databases; normal test
execution does not require API credentials or network access.
`tests/test_api.py` publishes a temporary bundle from
`data/music_metadata_100.db` and is skipped if that file is absent. TypeScript
parity tests: `cd ts && npm install && npm test`.
