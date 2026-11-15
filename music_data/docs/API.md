# Music data API

A read-only library for getting music data from Python or TypeScript with plain
function calls. No SQL, no HTTP server, no pipeline knowledge needed. Both
libraries read the same **bundle** and return the same JSON-shaped data.

User data (ratings, comparisons, Elo) is not part of this. Store it in your own
database and refer to artists by MBID.

## 1. Publish a bundle

```bash
python -m music_data publish --output-dir api_bundle
```

This reads `data/music_metadata_100.db` (read-only) and writes:

| File | Contents |
|---|---|
| `music.db` | Slim SQLite: artists, aliases, genres, tracks, track credits, Deezer track IDs, listener-similarity edges |
| `audio.f16.npy` + `audio_ids.json` | Artist audio vectors (MuQ-MuLan, 512-d, L2-normalised, float16) |
| `text.f16.npy` + `text_ids.json` | Artist text vectors (all-MiniLM, 384-d) |
| `tracks.f16.npy` + `track_ids.json` | Per-track audio vectors; `recording_id` = `tracks.id` in `music.db` |
| `manifest.json` | `bundle_version`, counts, model specs, build time |

The bundle has no preview URLs, local paths or audio files. Don't commit it
(`api_bundle/` is gitignored). To get popularity numbers, run
`python -m music_data collect-popularity` before publishing.

## 2. Open it

**Python** (needs only `numpy`):

```python
from music_data.api import MusicData

md = MusicData("api_bundle")
```

Results are frozen dataclasses. Call `.to_dict()` to get plain JSON.

**TypeScript** (`ts/`, Node ≥ 22.13, no runtime dependencies):

```ts
import { MusicData } from "music-data"; // or "./ts/src/index.js"

const md = new MusicData("api_bundle");
```

The functions are the same in camelCase. Returned objects keep the same
snake_case field names as the Python `to_dict()` output.

## Rules

- Artists are keyed by **MBID** everywhere.
- **Catalogue** artists (`in_catalog: true`) are fully collected. Uncatalogued
  ("stub") artists only have `mbid` and `name`.
- Unknown input returns `None`/`null` or an empty list. It never raises.
- Scores are comparable **only within one signal**.
- Signals:
  - `listeners`: ListenBrainz co-listening, normalised so the strongest
    neighbour scores 1.
  - `audio` / `text`: cosine similarity of the artist vectors.
  - `genre`: cosine similarity of the weighted genre profiles.
- Popularity: `popularity` is the ListenBrainz listener count.
  `popularity_percentile` is the share of catalogue artists with that many
  listeners or fewer, from 0 to 1. Both are `null` when unknown.

## Functions

The examples are in Python. In TypeScript, swap the name to camelCase, e.g.
`md.similarArtists(mbid, "audio", 10)`.

```python
RADIOHEAD = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
```

**`search_artists(query, limit=20, include_uncatalogued=True)`**: matches
names and aliases. Matching ignores case and accents and tolerates prefixes and
typos. Catalogue artists rank first.
```python
md.search_artists("radiohed", limit=3)
# [SearchResult(mbid='a74b…', name='Radiohead', in_catalog=True, score=0.533333, matched='Radiohead')]
```

**`get_artist(mbid)` / `get_artists(mbids)`**: the full record for catalogue
artists, `name` + `in_catalog=False` for stubs, `None` for unknown MBIDs.
```python
md.get_artist(RADIOHEAD).to_dict()
# {'mbid': …, 'name': 'Radiohead', 'in_catalog': True, 'country': 'GB', 'begin_year': 1991,
#  'popularity': 461120.0, 'popularity_percentile': 1.0, 'has_audio': True, …,
#  'aliases': [...], 'genres': [{'genre': 'alternative rock', 'weight': 0.5}, …]}
```

**`get_artist_tracks(mbid)`**: tracks the artist is credited on, oldest first.
Each has `id`, `title`, `duration_ms`, `release_date` and `has_preview`.
```python
md.get_artist_tracks(RADIOHEAD)[0].title
```

**`similar_artists(mbid, signal, limit=20)`**: catalogue artists only, each
with `score` and `signal`. Returns `[]` if the artist lacks that signal.
```python
md.similar_artists(RADIOHEAD, "audio", limit=5)
```

**`similarity_between(mbid_a, mbid_b)`**: a score for each signal (`None`
where a signal is missing) plus the shared genres. Use it for recommendation
explanations.
```python
md.similarity_between(RADIOHEAD, other).to_dict()
# {'scores': {'listeners': 0.12, 'audio': 0.93, 'text': 0.57, 'genre': None}, 'shared_genres': []}
```

**`nearest_to_vector(vector, signal, limit=20, exclude=())`**: nearest
catalogue artists to a taste-profile vector. `signal` is `audio` or `text`.
The vector is normalised for you.
```python
taste = md.get_vector(a, "audio") + md.get_vector(b, "audio")
md.nearest_to_vector(taste, "audio", limit=10, exclude=[a, b])
```

**`get_vector(mbid, signal)`**: the raw vector, or `None`. Python returns a
float32 numpy array; TypeScript returns a `Float32Array`.

**`list_genres()`**: `[{genre, artist_count}]`, most common first.
**`artists_by_genre(genre, limit=50)`**: `[{mbid, name, weight}]`.
```python
md.artists_by_genre("shoegaze", 10)
```

**`random_artists(n, genre=None, seed=None)`**: for head-to-head matchups.
The same seed gives the same artists in Python and TypeScript.
```python
md.random_artists(2, genre="indie rock", seed=123)
```

**`get_preview_url(track_id)`**: asks Deezer for a fresh 30-second preview URL,
using the stored Deezer track ID. Results are cached for 10 minutes. This is
the only function that uses the network, and it returns `None` on failure.
In TypeScript it is async.
```python
md.get_preview_url(md.get_artist_tracks(RADIOHEAD)[0].id)
```

**`resolve_artist(name)`**: the best MBID for a name from outside the dataset,
with a `confidence` from 0 to 1. Returns `None` below 0.5.
```python
md.resolve_artist("Radio Head")  # Resolution(mbid='a74b…', name='Radiohead', in_catalog=True, confidence=0.95)
```

**`dataset_info()`**: counts and bundle version.

## Tests and cross-language parity

```bash
python -m pytest tests/test_api.py               # publishes a temp bundle, ~5s
python scripts/gen_api_fixtures.py               # regenerate ts/test/fixtures.json from api_bundle/
cd ts && npm install && npm test                  # TS must match the fixtures (floats to 1e-4)
```

Regenerate the fixtures whenever you republish the bundle or change scoring
rules. Scoring, rounding and tie-breaks live in both `src/music_data/api.py`
and `ts/src/index.ts`, so change them together.

Known limitation: name normalisation can differ between the two languages for
a few rare Unicode combining marks.
