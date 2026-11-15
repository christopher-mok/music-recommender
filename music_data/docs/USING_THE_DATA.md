# Using the Data

How to get at what this pipeline produces: the SQLite metadata DB and the embeddings.

## Where the data is

| File | What's in it |
|------|--------------|
| `data/music_metadata_100.db` | **The real dataset.** 12,827 artists (1,753 fully collected, the other 11,074 are "stub" artists that only appear as similarity targets or co-credits), 10.4k recordings, 75k similarity edges, and 8,051 audio preview clips (as of 2026-11-15). Other modules should read the published API bundle instead; see [API.md](API.md). |
| `data/music_metadata.db` | Small early test DB (10 complete artists). Only used if you pass `--db-path data/music_metadata.db`. |
| `.cache/music_data/audio/` | Downloaded 30s Deezer previews (private, don't redistribute). |
| `.cache/music_data/embeddings/bundle/` | Default `export` output. |

Commands use `data/music_metadata_100.db` by default. Override it with `--db-path` or `MUSIC_DATA_DB`.

## Querying the SQL database

Open it with any SQLite client (`sqlite3`, DB Browser for SQLite, the VS Code SQLite extension) or from Python:

```python
import sqlite3
conn = sqlite3.connect("data/music_metadata_100.db")
conn.row_factory = sqlite3.Row
```

### Main tables

- `artists`: one row per artist. Use `id` for joins and `mbid` as the stable external ID. Filter on `collection_status = 'complete'` to get fully collected artists.
- `recordings`, `recording_artists`: tracks and who is credited on them.
- `artist_similarities`: directed edges `source_artist_id → target_artist_id` with a ListenBrainz `score`.
- `artist_genres` (**view**): cleaned genres with `weight` in (0, 1]. Use this instead of raw `artist_tags`.
- `artist_tags`, `tags`: raw MusicBrainz tags, including noisy ones.
- `audio_clips`: preview clip per recording (`track_id` = `recordings.id`, `local_path` = the mp3 on disk).
- `preview_misses`: recordings where no preview was found.

### Example queries

```sql
-- Complete artists with their top genre
SELECT a.name, a.country, g.genre, g.weight
FROM artists a
JOIN artist_genres g ON g.artist_id = a.id
WHERE a.collection_status = 'complete'
  AND g.weight = 1.0;

-- Artists most similar to X
SELECT t.name, s.score
FROM artist_similarities s
JOIN artists src ON src.id = s.source_artist_id
JOIN artists t   ON t.id   = s.target_artist_id
WHERE src.name = 'Radiohead'
ORDER BY s.score DESC
LIMIT 20;

-- An artist's tracks, and whether each one has an audio preview
SELECT r.title, r.duration_ms, c.local_path
FROM recordings r
JOIN recording_artists ra ON ra.recording_id = r.id
JOIN artists a ON a.id = ra.artist_id
LEFT JOIN audio_clips c ON c.track_id = r.id
WHERE a.name = 'Radiohead';

-- Genre counts across complete artists
SELECT g.genre, COUNT(*) AS n
FROM artist_genres g JOIN artists a ON a.id = g.artist_id
WHERE a.collection_status = 'complete'
GROUP BY g.genre ORDER BY n DESC;
```

Quick summaries: `python -m music_data stats` and `python -m music_data report`.

## Building embeddings

You need the extras first: `pip install -e ".[embed]"` (or `.[muq]` for MuQ-MuLan, which needs `transformers<5`, so use a separate venv).

Models: `embeddinggemma2-audio`, `muq-mulan` (audio), `allminilm`, `text-fallback` (text), and `fake` / `audio-fake` (for tests).

```bash
python -m music_data embeddings previews                       # fetch/download clips (already done for the 100 DB)
python -m music_data embeddings embed --model embeddinggemma2-audio --modality audio
python -m music_data embeddings embed --model allminilm --modality text
python -m music_data embeddings aggregate --spec-hash <hash> --pooling mean
python -m music_data embeddings export --spec-hash <hash> --output-dir out/gemma_audio
python -m music_data embeddings export --spec-hash <hash> --level track --output-dir out/gemma_tracks
```

Only fully collected artists get artist vectors and text embeddings (1,701 of 1,753 have audio vectors, all 1,753 have text vectors). Stub artists are skipped.

Each step is resumable: a `(spec_hash, input_hash)` pair is never embedded twice. If you change any model setting you get a new `spec_hash`, and the old vectors are kept.

## Querying embeddings

### Embedding tables

- `embedding_specs`: one row per model+settings ("run"). Find your `spec_hash` here.
- `embeddings`: raw vectors. For audio there's one row per clip (`entity_type='track'`, `entity_id` = `recordings.id`), and `vectors` has shape `(n_windows, dim)`. For text there's one row per artist (`entity_id` = `artists.mbid`).
- `artist_vectors`: one pooled vector per artist (`mbid`, `pooling`).
- `embedding_failures`: clips that failed to embed.

Vectors are stored as BLOBs in a custom format (shape header + float32), so decode them with the helper:

```python
import numpy as np, sqlite3
from music_data.embeddings.blob_helpers import blob_to_array

conn = sqlite3.connect("data/music_metadata_100.db")
print(conn.execute("SELECT spec_hash, model_name, modality, dim FROM embedding_specs").fetchall())
SPEC = "..."  # pick one

rows = conn.execute("""
    SELECT v.mbid, a.name, v.vector
    FROM artist_vectors v JOIN artists a ON a.mbid = v.mbid
    WHERE v.spec_hash = ? AND v.pooling = 'mean'
""", (SPEC,)).fetchall()
names = [r[1] for r in rows]
X = np.stack([blob_to_array(r[2]).ravel() for r in rows])
X /= np.linalg.norm(X, axis=1, keepdims=True)

def nearest(name, k=10):
    i = names.index(name)
    sims = X @ X[i]
    return [(names[j], float(sims[j])) for j in np.argsort(-sims)[1:k+1]]

print(nearest("Radiohead"))
```

### Exported bundle (no SQLite needed)

```python
import json, numpy as np
X = np.load("out/gemma_audio/vectors.f16.npy").astype(np.float32)   # (N, dim)
ids = json.load(open("out/gemma_audio/ids.json"))                    # artist MBIDs, same order
# With --level track, ids.json holds one object per row:
# {recording_id, recording_mbid, title, artist_mbid, artist_name}
manifest = json.load(open("out/gemma_audio/manifest.json"))          # model + spec info
```

Only compare vectors that share a `spec_hash`. Vectors from different models live in different spaces.

## Visualizer

Run the interactive web visualizer:

```bash
pip install -e ".[viz]"
streamlit run scripts/viz.py
```

The visualizer auto-detects the database path from `src/music_data/config.py` (or `MUSIC_DATA_DB` env var). It opens the DB in **read-only mode** (`mode=ro`) and writes nothing to the database or pipeline cache directories — all projections are cached under `.cache/music_viz/`.

Four tabs are available:

| Tab | What it does |
|-----|-------------|
| **📋 Table Browser** | Paginated browser of every table with column filters and full-text search. |
| **📊 Coverage Stats** | Artist/track counts, media coverage, genre distribution, quota gaps, and fetch provenance. |
| **🗺️ Embedding Map** | Interactive 2D scatter plot of track embeddings (UMAP → t-SNE → PCA fallback). Click a point to select it. |
| **🎧 Audio Playback** | Play 30-second Deezer preview clips stored locally on disk. |

The embedding map uses **UMAP** when available, falling back to **t-SNE** or **PCA**. Cache lives in `.cache/music_viz/projection_<hash>.npz`. You can drag/zoom the map and hover for tooltips.

Clicking a point on the map highlights it and offers a "🎧 Play this track" button that opens the Audio Playback tab with that track pre-selected.
