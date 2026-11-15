"""Publish a read-only API bundle from the pipeline database.

Produces a slim SQLite file, float16 embedding arrays, and a manifest.
This module is **read-only** against the source database — it never
writes to anything under ``data/``.

Output layout::

    <output_dir>/
        music.db          # slim relational data
        audio.f16.npy     # (N_complete × 512), L2-normalised
        text.f16.npy      # (N_complete × 384), L2-normalised
        tracks.f16.npy    # (N_tracks × 512), L2-normalised
        audio_ids.json    # parallel MBID list (only complete artists)
        text_ids.json     # parallel MBID list
        track_ids.json    # parallel recording IDs
        manifest.json
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

AUDIO_SPEC_HASH = "cd8ee60e4b1799fa63e35d4f9301298ff02df2ca8d668a1753dd33052d32fe69"
TEXT_SPEC_HASH = "4e64fdab4cf8c87f1fb178bc0bca6a99727bfbcb28a084d3678f38efc423b669"
AUDIO_DIM = 512
TEXT_DIM = 384


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# Blob helpers (inline to avoid import cycle)
# ----------------------------------------------------------------------

def _blob_to_array(blob: bytes) -> np.ndarray:
    """Decode a vector blob to float32 numpy array.

    Blob format: <int32 shape_len><int32 *shape><float32 *data>
    """
    if len(blob) < 4:
        return np.zeros(AUDIO_DIM, dtype=np.float32)
    buf = __import__("io").BytesIO(blob)
    shape_len = np.frombuffer(buf.read(4), dtype=np.int32)[0]
    dims = np.frombuffer(buf.read(4 * shape_len), dtype=np.int32)
    data = np.frombuffer(buf.read(), dtype=np.float32)
    return data.reshape(dims.tolist())


# ----------------------------------------------------------------------
# music.db creation
# ----------------------------------------------------------------------

def _build_music_db(src_conn, out_dir: Path) -> dict[str, int]:
    """Create the slim music.db inside the bundle."""
    music_path = out_dir / "music.db"

    # Build the bundle DB
    music_path_str = str(music_path)
    logger.info("Creating bundle DB at: %s", music_path_str)
    if music_path.exists():
        logger.warning("Removing stale music.db: %s", music_path)
        music_path.unlink()
    bundle_conn = sqlite3.connect(music_path_str)
    bundle_conn.autocommit = False  # Python 3.12+ requires explicit autocommit=False
    logger.info("Bundle connection opened (read-only=%s)", 
                bundle_conn.execute("PRAGMA query_only").fetchone()[0])
    
    bundle_conn.execute("""
        CREATE TABLE artists (
            mbid TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_name TEXT,
            artist_type TEXT,
            country TEXT,
            begin_year INTEGER,
            end_year INTEGER,
            gender TEXT,
            collection_status TEXT NOT NULL,
            popularity REAL,
            popularity_listens REAL,
            popularity_percentile REAL,
            has_audio INTEGER NOT NULL DEFAULT 0,
            has_text INTEGER NOT NULL DEFAULT 0,
            has_listeners INTEGER NOT NULL DEFAULT 0,
            has_genre INTEGER NOT NULL DEFAULT 0,
            in_catalog INTEGER NOT NULL DEFAULT 1
        )
    """)
    
    bundle_conn.execute("""
        CREATE TABLE aliases (
            artist_mbid TEXT NOT NULL,
            name TEXT NOT NULL,
            sort_name TEXT,
            alias_type TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (artist_mbid) REFERENCES artists(mbid)
        )
    """)
    bundle_conn.execute("""
        CREATE TABLE genres (
            artist_mbid TEXT NOT NULL,
            genre TEXT NOT NULL,
            weight REAL NOT NULL,
            FOREIGN KEY (artist_mbid) REFERENCES artists(mbid)
        )
    """)
    bundle_conn.execute("""
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY,
            recording_mbid TEXT,
            title TEXT NOT NULL,
            artist_mbid TEXT NOT NULL,
            duration_ms INTEGER,
            release_date TEXT,
            release_id TEXT,
            has_preview INTEGER NOT NULL DEFAULT 0,
            deezer_id TEXT,
            FOREIGN KEY (artist_mbid) REFERENCES artists(mbid)
        )
    """)
    bundle_conn.execute("""
        CREATE TABLE track_artists (
            recording_id INTEGER NOT NULL,
            artist_mbid TEXT NOT NULL,
            credit_name TEXT,
            FOREIGN KEY (recording_id) REFERENCES tracks(id),
            FOREIGN KEY (artist_mbid) REFERENCES artists(mbid)
        )
    """)
    bundle_conn.execute("""
        CREATE TABLE similarity_edges (
            source_mbid TEXT NOT NULL,
            target_mbid TEXT NOT NULL,
            provider TEXT NOT NULL,
            score REAL,
            FOREIGN KEY (source_mbid) REFERENCES artists(mbid),
            FOREIGN KEY (target_mbid) REFERENCES artists(mbid)
        )
    """)
    
    # Create indexes
    bundle_conn.execute("CREATE INDEX idx_aliases_artist ON aliases(artist_mbid)")
    bundle_conn.execute("CREATE INDEX idx_genres_artist ON genres(artist_mbid)")
    bundle_conn.execute("CREATE INDEX idx_genres_genre ON genres(genre)")
    bundle_conn.execute("CREATE INDEX idx_tracks_artist ON tracks(artist_mbid)")
    bundle_conn.execute("CREATE INDEX idx_sim_source ON similarity_edges(source_mbid)")
    bundle_conn.execute("CREATE INDEX idx_sim_target ON similarity_edges(target_mbid)")
    
    logger.info("Bundle DB schema created")

    counts: dict[str, int] = {}

    # ---- Fetch all data in bulk ----
    # Complete artists
    complete_artists = src_conn.execute("""
        SELECT a.mbid, a.name, a.sort_name, a.artist_type, a.country,
               a.begin_year, a.end_year, a.gender,
               a.collection_status
        FROM artists a
        WHERE a.collection_status = 'complete'
    """).fetchall()

    complete_mbid_list = [row[0] for row in complete_artists]

    # Stub artists
    stub_artists = src_conn.execute("""
        SELECT a.mbid, a.name
        FROM artists a
        WHERE a.collection_status = 'stub'
    """).fetchall()

    # Popularity data for complete artists
    placeholders = ", ".join("?" for _ in complete_mbid_list)
    pop_rows = src_conn.execute(f"""
        SELECT ap.artist_id, a.mbid, ap.measure, ap.value
        FROM artist_popularity ap
        JOIN artists a ON a.id = ap.artist_id
        WHERE a.collection_status = 'complete'
          AND a.mbid IN ({placeholders})
    """, complete_mbid_list).fetchall()

    # Build popularity lookup: mbid -> {measure: value}
    pop_lookup: dict[str, dict[str, float]] = {}
    for artist_id, mbid, measure, value in pop_rows:
        if mbid not in pop_lookup:
            pop_lookup[mbid] = {}
        if value is not None:
            pop_lookup[mbid][measure] = float(value)

    # popularity_percentile: fraction of catalogue artists (with a known
    # listener count) whose count is <= this one. 0 listeners is a real value.
    import bisect
    all_listeners = sorted(v["listeners"] for v in pop_lookup.values() if "listeners" in v)
    n_artists_with_listeners = len(all_listeners)

    def _percentile(listeners: float) -> float | None:
        if not n_artists_with_listeners:
            return None
        return round(bisect.bisect_right(all_listeners, listeners) / n_artists_with_listeners, 6)

    # Embedding coverage for complete artists
    audio_covers = set()
    text_covers = set()

    audio_rows = src_conn.execute(f"""
        SELECT v.mbid FROM artist_vectors v
        WHERE v.spec_hash = ? AND v.pooling = 'mean' AND v.mbid IN ({placeholders})
    """, [AUDIO_SPEC_HASH] + complete_mbid_list).fetchall()
    audio_covers = {row[0] for row in audio_rows}

    text_rows = src_conn.execute(f"""
        SELECT v.mbid FROM artist_vectors v
        WHERE v.spec_hash = ? AND v.pooling = 'mean' AND v.mbid IN ({placeholders})
    """, [TEXT_SPEC_HASH] + complete_mbid_list).fetchall()
    text_covers = {row[0] for row in text_rows}

    # Aliases for complete artists
    placeholders_complete = ", ".join("?" for _ in complete_mbid_list)
    alias_rows = src_conn.execute(f"""
        SELECT aa.artist_id, a.mbid, aa.name, aa.sort_name,
               aa.alias_type, aa.language
        FROM artist_aliases aa
        JOIN artists a ON a.id = aa.artist_id
        WHERE a.collection_status = 'complete'
          AND a.mbid IN ({placeholders_complete})
    """, complete_mbid_list).fetchall()

    # Genres from the view for complete artists
    genre_rows = src_conn.execute(f"""
        SELECT ag.artist_id, a.mbid, ag.genre, ag.weight
        FROM artist_genres ag
        JOIN artists a ON a.id = ag.artist_id
        WHERE a.collection_status = 'complete'
          AND a.mbid IN ({placeholders_complete})
    """, complete_mbid_list).fetchall()

    # Similarity edges (only between complete artists)
    sim_rows = src_conn.execute("""
        SELECT src.mbid AS source_mbid, tgt.mbid AS target_mbid,
               s.provider, s.score
        FROM artist_similarities s
        JOIN artists src ON src.id = s.source_artist_id AND src.collection_status = 'complete'
        JOIN artists tgt ON tgt.id = s.target_artist_id AND tgt.collection_status = 'complete'
    """).fetchall()

    genre_mbids = {g[1] for g in genre_rows}

    # ---- Insert complete artists ----
    artist_insert_rows = []
    for row in complete_artists:
        mbid, name, sort_name, artist_type, country, \
            begin_year, end_year, gender, collection_status = row

        pop = pop_lookup.get(mbid, {})
        listens = pop.get("listens")
        listeners = pop.get("listeners")
        percentile = _percentile(listeners) if listeners is not None else None

        artist_insert_rows.append(
            (
                mbid, name, sort_name, artist_type, country,
                begin_year, end_year, gender, collection_status,
                listeners,
                listens,
                percentile,
                1 if mbid in audio_covers else 0,
                1 if mbid in text_covers else 0,
                1 if "listeners" in pop else 0,
                1 if mbid in genre_mbids else 0,
                1,
            )
        )

    bundle_conn.executemany(
        "INSERT INTO artists VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        artist_insert_rows,
    )
    counts["complete_artists"] = len(artist_insert_rows)

    # ---- Insert stub artists ----
    stub_rows = []
    for row in stub_artists:
        mbid, name = row
        stub_rows.append(
            (mbid, name, None, None, None, None, None, None, "stub",
             None, None, None, 0, 0, 0, 0, 0)
        )
    bundle_conn.executemany(
        "INSERT INTO artists VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        stub_rows,
    )
    counts["stub_artists"] = len(stub_rows)

    # ---- Insert aliases ----
    bundle_conn.executemany(
        "INSERT INTO aliases (artist_mbid, name, sort_name, alias_type, language) VALUES (?,?,?,?,?)",
        [(row[1], row[2], row[3], row[4], row[5]) for row in alias_rows],
    )
    counts["aliases"] = len(alias_rows)

    # ---- Insert genres ----
    bundle_conn.executemany(
        "INSERT INTO genres (artist_mbid, genre, weight) VALUES (?,?,?)",
        [(row[1], row[2], row[3]) for row in genre_rows],
    )
    counts["genres"] = len(genre_rows)

    # ---- Insert tracks ----
    # Get all recordings for complete artists with artist mapping
    # One row per recording credited to a complete artist. artist_mbid is the
    # first-credited complete artist. Track ids are the source recording ids so
    # they line up with track_ids.json.
    all_track_rows = src_conn.execute("""
        SELECT r.id, r.mbid, r.title, r.duration_ms, r.first_release_date,
               (SELECT MIN(rr.release_id) FROM recording_releases rr
                WHERE rr.recording_id = r.id) AS release_id,
               (SELECT ac.provider_track_id FROM audio_clips ac
                WHERE ac.track_id = r.id AND ac.provider = 'deezer'
                  AND ac.provider_track_id IS NOT NULL LIMIT 1) AS deezer_id,
               (SELECT a.mbid FROM recording_artists ra
                JOIN artists a ON a.id = ra.artist_id AND a.collection_status = 'complete'
                WHERE ra.recording_id = r.id
                ORDER BY ra.credit_order LIMIT 1) AS artist_mbid
        FROM recordings r
        WHERE EXISTS (SELECT 1 FROM recording_artists ra
                      JOIN artists a ON a.id = ra.artist_id
                      WHERE ra.recording_id = r.id AND a.collection_status = 'complete')
        ORDER BY r.id
    """).fetchall()

    # Deduplicate: one row per recording (keep first artist)
    seen_rec_ids: set[int] = set()
    track_rows_deduped: list[tuple] = []
    for row in all_track_rows:
        if row[0] not in seen_rec_ids:
            seen_rec_ids.add(row[0])
            track_rows_deduped.append(row)

    track_id_counter = 0
    rec_to_bundle_id: dict[int, int] = {}

    for track_row in track_rows_deduped:
        rec_id, rec_mbid, title, duration_ms, release_date, \
            release_id, deezer_id, artist_mbid = track_row

        track_id_counter += 1
        rec_to_bundle_id[rec_id] = rec_id

        bundle_conn.execute(
            "INSERT INTO tracks VALUES (?,?,?,?,?,?,?,?,?)",
            (rec_id, rec_mbid, title, artist_mbid, duration_ms, release_date,
             str(release_id) if release_id is not None else None,
             1 if deezer_id else 0,
             deezer_id),
        )

    counts["tracks"] = track_id_counter

    # ---- Insert track_artists ----
    recording_artist_rows = src_conn.execute("""
        SELECT ra.recording_id, a.mbid, ra.credit_name
        FROM recording_artists ra
        JOIN artists a ON a.id = ra.artist_id AND a.collection_status = 'complete'
    """).fetchall()

    # Convert src recording_id to bundle track_id
    track_artist_inserts = []
    for rec_id, artist_mbid, credit_name in recording_artist_rows:
        if rec_id in rec_to_bundle_id:
            track_artist_inserts.append(
                (rec_to_bundle_id[rec_id], artist_mbid, credit_name)
            )

    bundle_conn.executemany(
        "INSERT INTO track_artists (recording_id, artist_mbid, credit_name) VALUES (?,?,?)",
        track_artist_inserts,
    )
    counts["track_artists"] = len(track_artist_inserts)

    # ---- Insert similarity edges ----
    bundle_conn.executemany(
        "INSERT INTO similarity_edges (source_mbid, target_mbid, provider, score) VALUES (?,?,?,?)",
        [(row[0], row[1], row[2], row[3]) for row in sim_rows],
    )
    counts["similarity_edges"] = len(sim_rows)

    bundle_conn.commit()
    bundle_conn.close()

    logger.info("Wrote music.db: %s", music_path)
    return counts


# ----------------------------------------------------------------------
# Embedding export
# ----------------------------------------------------------------------

def _export_audio_vectors(src_conn, out_dir: Path) -> int:
    """Export audio vectors (muq-mulan) to audio.f16.npy."""
    rows = src_conn.execute("""
        SELECT v.mbid, v.vector
        FROM artist_vectors v
        WHERE v.spec_hash = ? AND v.pooling = 'mean'
          AND EXISTS (SELECT 1 FROM artists a WHERE a.mbid = v.mbid AND a.collection_status = 'complete')
        ORDER BY v.mbid
    """, (AUDIO_SPEC_HASH,)).fetchall()

    if not rows:
        raise ValueError("No audio vectors found")

    dim = AUDIO_DIM
    vectors = np.zeros((len(rows), dim), dtype=np.float32)
    mbids: list[str] = []

    for i, row in enumerate(rows):
        mbid = row[0]
        blob = row[1]
        arr = _blob_to_array(blob)
        # Ensure L2-normalised
        norm = np.linalg.norm(arr.ravel())
        if norm > 0:
            arr = arr / norm
        vectors[i] = arr.ravel()
        mbids.append(mbid)

    vectors_f16 = vectors.astype(np.float16)
    path = out_dir / "audio.f16.npy"
    np.save(str(path), vectors_f16)

    ids_path = out_dir / "audio_ids.json"
    ids_path.write_text(json.dumps(mbids, indent=2), encoding="utf-8")

    logger.info("Wrote audio.f16.npy: %d vectors × %d dim → %s", len(rows), dim, path)
    return len(rows)


def _export_text_vectors(src_conn, out_dir: Path) -> int:
    """Export text vectors (allminilm) to text.f16.npy."""
    rows = src_conn.execute("""
        SELECT v.mbid, v.vector
        FROM artist_vectors v
        WHERE v.spec_hash = ? AND v.pooling = 'mean'
          AND EXISTS (SELECT 1 FROM artists a WHERE a.mbid = v.mbid AND a.collection_status = 'complete')
        ORDER BY v.mbid
    """, (TEXT_SPEC_HASH,)).fetchall()

    if not rows:
        raise ValueError("No text vectors found")

    dim = TEXT_DIM
    vectors = np.zeros((len(rows), dim), dtype=np.float32)
    mbids: list[str] = []

    for i, row in enumerate(rows):
        mbid = row[0]
        blob = row[1]
        arr = _blob_to_array(blob)
        norm = np.linalg.norm(arr.ravel())
        if norm > 0:
            arr = arr / norm
        vectors[i] = arr.ravel()
        mbids.append(mbid)

    vectors_f16 = vectors.astype(np.float16)
    path = out_dir / "text.f16.npy"
    np.save(str(path), vectors_f16)

    ids_path = out_dir / "text_ids.json"
    ids_path.write_text(json.dumps(mbids, indent=2), encoding="utf-8")

    logger.info("Wrote text.f16.npy: %d vectors × %d dim → %s", len(rows), dim, path)
    return len(rows)


def _export_track_vectors(src_conn, out_dir: Path) -> tuple[int, list[dict]]:
    """Export track-level audio vectors to tracks.f16.npy.

    Returns (count, ids_list) where ids_list is a list of dicts with
    recording_id, recording_mbid, title, artist_mbid, artist_name.
    """
    rows = src_conn.execute("""
        SELECT e.vectors, e.spec_hash,
               r.id AS recording_id, r.mbid AS recording_mbid, r.title,
               (SELECT a.mbid FROM recording_artists ra2
                JOIN artists a ON a.id = ra2.artist_id
                WHERE ra2.recording_id = r.id ORDER BY ra2.credit_order LIMIT 1) AS artist_mbid,
               (SELECT a.name FROM recording_artists ra2
                JOIN artists a ON a.id = ra2.artist_id
                WHERE ra2.recording_id = r.id ORDER BY ra2.credit_order LIMIT 1) AS artist_name
        FROM embeddings e
        JOIN audio_clips ac ON ac.audio_sha256 = e.input_hash
        JOIN recordings r ON r.id = ac.track_id
        WHERE e.spec_hash = ?
          AND EXISTS (SELECT 1 FROM recording_artists ra
                      JOIN artists a ON a.id = ra.artist_id
                      WHERE ra.recording_id = r.id AND a.collection_status = 'complete')
        ORDER BY r.id
    """, (AUDIO_SPEC_HASH,)).fetchall()

    if not rows:
        raise ValueError("No track embeddings found")

    dim = AUDIO_DIM
    vectors = np.zeros((len(rows), dim), dtype=np.float32)
    ids: list[dict] = []

    for i, row in enumerate(rows):
        blob = row[0]
        # Embeddings can be multi-window; pool them
        arr = _blob_to_array(blob)
        if arr.ndim == 2:
            arr = arr.mean(axis=0)
        norm = np.linalg.norm(arr.ravel())
        if norm > 0:
            arr = arr / norm
        vectors[i] = arr.ravel()
        ids.append({
            "recording_id": int(row[2]),
            "recording_mbid": row[3] if row[3] else None,
            "title": row[4] if row[4] else None,
            "artist_mbid": row[5] if row[5] else None,
            "artist_name": row[6] if row[6] else None,
        })

    vectors_f16 = vectors.astype(np.float16)
    path = out_dir / "tracks.f16.npy"
    np.save(str(path), vectors_f16)

    ids_path = out_dir / "track_ids.json"
    ids_path.write_text(json.dumps(ids, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("Wrote tracks.f16.npy: %d vectors × %d dim → %s", len(rows), dim, path)
    return len(rows), ids


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------

def _write_manifest(out_dir: Path, db_counts: dict, audio_n: int,
                    text_n: int, track_n: int, spec_rows: dict) -> None:
    """Write manifest.json with bundle metadata."""
    manifest = {
        "bundle_version": "1.0.0",
        "build_time": _utc_now(),
        "source": "music_data pipeline",
        "artists": {
            "complete": db_counts.get("complete_artists", 0),
            "stub": db_counts.get("stub_artists", 0),
            "total": db_counts.get("complete_artists", 0) + db_counts.get("stub_artists", 0),
        },
        "tracks": db_counts.get("tracks", 0),
        "genres": db_counts.get("genres", 0),
        "aliases": db_counts.get("aliases", 0),
        "similarity_edges": db_counts.get("similarity_edges", 0),
        "embeddings": {
            "audio": {"count": audio_n, "dim": AUDIO_DIM, "model": "muq-mulan"},
            "text": {"count": text_n, "dim": TEXT_DIM, "model": "allminilm"},
            "tracks": {"count": track_n, "dim": AUDIO_DIM, "model": "muq-mulan"},
        },
    }

    # Include embedding spec metadata
    for key, spec in spec_rows.items():
        manifest[f"spec_{key}"] = spec

    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Wrote manifest.json → %s", path)


# ----------------------------------------------------------------------
# Spec metadata helper
# ----------------------------------------------------------------------

def _get_spec_rows(src_conn) -> dict[str, dict]:
    """Fetch spec metadata from the pipeline DB."""
    specs: dict[str, dict] = {}
    for spec_hash in (AUDIO_SPEC_HASH, TEXT_SPEC_HASH):
        row = src_conn.execute(
            "SELECT spec_hash, spec_json, model_name, modality, dim FROM embedding_specs WHERE spec_hash = ?",
            (spec_hash,),
        ).fetchone()
        if row:
            specs[row[2]] = {
                "spec_hash": row[0],
                "spec": json.loads(row[1]) if row[1] else None,
                "model_name": row[2],
                "modality": row[3],
                "dim": row[4],
            }
    return specs


# ----------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------

def publish(
    src_db_path: str | Path,
    output_dir: str | Path,
) -> dict:
    """Publish a full API bundle from the pipeline database.

    Args:
        src_db_path: Path to the pipeline SQLite database.
        output_dir: Directory where the bundle will be written.

    Returns:
        Dictionary with summary statistics.
    """
    src_db_path = Path(src_db_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not src_db_path.exists():
        raise FileNotFoundError(f"Source database not found: {src_db_path}")

    logger.info("Publishing bundle from %s → %s", src_db_path, out_dir)
    start = time.time()

    # Open source DB in read-only mode
    src_conn = sqlite3.connect(f"file:{src_db_path}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row

    try:
        # 1. Spec metadata
        spec_rows = _get_spec_rows(src_conn)

        # 2. Create music.db
        db_counts = _build_music_db(src_conn, out_dir)

        # 3. Export embeddings
        audio_n = _export_audio_vectors(src_conn, out_dir)
        text_n = _export_text_vectors(src_conn, out_dir)
        track_n, _track_ids = _export_track_vectors(src_conn, out_dir)

        # 4. Write manifest
        _write_manifest(out_dir, db_counts, audio_n, text_n, track_n, spec_rows)

        elapsed = time.time() - start
        summary = {
            "elapsed_seconds": round(elapsed, 2),
            "output_dir": str(out_dir),
            "artists": {
                "complete": db_counts.get("complete_artists", 0),
                "stub": db_counts.get("stub_artists", 0),
            },
            "tracks": db_counts.get("tracks", 0),
            "embeddings": {
                "audio": audio_n,
                "text": text_n,
                "tracks": track_n,
            },
        }

        logger.info("Publish completed in %.1fs", elapsed)
        return summary

    finally:
        src_conn.close()
