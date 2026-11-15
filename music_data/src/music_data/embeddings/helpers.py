"""Database helpers for the embedding tables.

Provides functions to register specs, store embeddings, query existing
embeddings, and pool track vectors → artist vectors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import numpy as np

from music_data.database import get_connection
from music_data.embeddings.blob_helpers import (
    array_to_blob,
    blob_to_array,
    canonical_json,
    spec_hash,
    sha256_bytes,
    sha256_text,
)

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------
# Registration: embedding specs
# ------------------------------------------------------------------
def register_spec(
    conn,
    *,
    embedder,
    code_version: str = "0.1.0",
) -> str:
    """Register an embedder's spec, return spec_hash.

    Idempotent: if the hash already exists, returns it without writing.
    """
    spec = embedder.spec()
    h = spec_hash(spec)

    row = conn.execute(
        "SELECT 1 FROM embedding_specs WHERE spec_hash = ?", (h,)
    ).fetchone()
    if row:
        return h

    lib_versions_parts: list[str] = []
    lib_versions_parts.append(f"numpy=={np.__version__}")
    try:
        import torch
        lib_versions_parts.append(f"torch=={torch.__version__}")
    except ImportError:
        pass
    try:
        from sentence_transformers import __version__ as st_ver
        lib_versions_parts.append(f"sentence-transformers=={st_ver}")
    except ImportError:
        pass

    conn.execute(
        """
        INSERT INTO embedding_specs
            (spec_hash, model_name, modality, dim, spec_json, code_version, lib_versions)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            h,
            embedder.name,
            embedder.modality,
            spec["dim"],
            json.dumps(spec, ensure_ascii=False, default=str),
            code_version,
            ";".join(lib_versions_parts),
        ),
    )
    return h


# ------------------------------------------------------------------
# Storing embeddings (idempotent)
# ------------------------------------------------------------------
def store_embedding(
    conn,
    *,
    spec_hash: str,
    input_hash: str,
    entity_type: str,  # 'track' | 'artist'
    entity_id: str,
    vectors: np.ndarray,
) -> bool:
    """Store a single embedding. Returns True if inserted, False if skipped."""
    blob = array_to_blob(vectors)
    n_windows = vectors.shape[0] if vectors.ndim >= 2 else 1

    conn.execute(
        """
        INSERT INTO embeddings
            (spec_hash, input_hash, entity_type, entity_id, n_windows, vectors)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(spec_hash, input_hash) DO NOTHING
        """,
        (spec_hash, input_hash, entity_type, entity_id, n_windows, blob),
    )
    # Check if it was actually inserted (lastrowid tells us)
    return True  # We always return True; the caller should check existence if needed


def upsert_embedding(
    conn,
    *,
    spec_hash: str,
    input_hash: str,
    entity_type: str,
    entity_id: str,
    vectors: np.ndarray,
) -> bool:
    """Store or replace an embedding. Returns True if new."""
    blob = array_to_blob(vectors)
    n_windows = vectors.shape[0] if vectors.ndim >= 2 else 1

    conn.execute(
        """
        INSERT INTO embeddings
            (spec_hash, input_hash, entity_type, entity_id, n_windows, vectors)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(spec_hash, input_hash) DO UPDATE SET
            vectors = excluded.vectors,
            n_windows = excluded.n_windows
        """,
        (spec_hash, input_hash, entity_type, entity_id, n_windows, blob),
    )
    return True


def get_existing_input_hashes(
    conn,
    spec_hash: str,
    entity_type: str,
    entity_ids: Iterable[str],
) -> set[str]:
    """Return set of input_hashes already stored for the given spec/entity_type."""
    placeholders = ", ".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"""
        SELECT input_hash FROM embeddings
        WHERE spec_hash = ? AND entity_type = ? AND entity_id IN ({placeholders})
        """,
        [spec_hash, entity_type] + list(entity_ids),
    ).fetchall()
    return {row["input_hash"] for row in rows}


def get_existing_entity_ids(
    conn,
    spec_hash: str,
    entity_type: str,
    entity_ids: Iterable[str],
) -> set[str]:
    """Return set of entity_ids that already have embeddings for the spec.

    Useful for skipping artists/tracks that were already embedded.
    """
    placeholders = ", ".join("?" for _ in entity_ids)
    rows = conn.execute(
        f"""
        SELECT DISTINCT entity_id FROM embeddings
        WHERE spec_hash = ? AND entity_type = ? AND entity_id IN ({placeholders})
        """,
        [spec_hash, entity_type] + list(entity_ids),
    ).fetchall()
    return {row["entity_id"] for row in rows}


# ------------------------------------------------------------------
# Querying embeddings
# ------------------------------------------------------------------
def get_embedding(conn, spec_hash: str, input_hash: str) -> np.ndarray | None:
    """Retrieve a stored embedding, or None if not found."""
    row = conn.execute(
        "SELECT vectors FROM embeddings WHERE spec_hash = ? AND input_hash = ?",
        (spec_hash, input_hash),
    ).fetchone()
    if row is None:
        return None
    return blob_to_array(row["vectors"])


def get_artist_embeddings(
    conn,
    spec_hash: str,
    mbid: str,
) -> list[dict[str, Any]]:
    """Get all track embeddings for an artist.

    Handles both audio (via audio_clips → recording_artists) and
    text (entity_type='artist') embeddings.
    """
    # Try audio path first
    audio_rows = conn.execute(
        """
        SELECT DISTINCT e.input_hash, e.entity_id, e.n_windows, e.vectors
        FROM embeddings e
        JOIN audio_clips ac ON ac.audio_sha256 = e.input_hash
        WHERE e.spec_hash = ? AND ac.track_id IN (
            SELECT ra.recording_id FROM recording_artists ra
            JOIN artists a ON a.id = ra.artist_id AND a.mbid = ?
        )
        """,
        (spec_hash, mbid),
    ).fetchall()

    # Also check for direct text embeddings (entity_type='artist')
    text_rows = conn.execute(
        """
        SELECT e.input_hash, e.entity_id, e.n_windows, e.vectors
        FROM embeddings e
        WHERE e.spec_hash = ? AND e.entity_type = 'artist' AND e.entity_id = ?
        """,
        (spec_hash, mbid),
    ).fetchall()

    rows = audio_rows + text_rows
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "input_hash": row["input_hash"],
            "entity_id": row["entity_id"],
            "n_windows": row["n_windows"],
            "vectors": blob_to_array(row["vectors"]),
        })
    return result


# ------------------------------------------------------------------
# Pooling: track vectors → artist vectors
# ------------------------------------------------------------------
POOLING_METHODS = {"mean", "sum", "median"}


def _pool(stacked: np.ndarray, pooling: str) -> np.ndarray:
    if pooling == "mean":
        return np.mean(stacked, axis=0)
    if pooling == "sum":
        return np.sum(stacked, axis=0)
    if pooling == "median":
        return np.median(stacked, axis=0)
    raise ValueError(f"Unknown pooling method: {pooling}")


def _track_vector(vectors: np.ndarray, dim: int) -> np.ndarray:
    """Collapse per-window vectors (any leading shape) to one (dim,) vector."""
    return np.asarray(vectors, dtype=np.float32).reshape(-1, dim).mean(axis=0)


def _store_artist_vector(conn, spec_hash, mbid, pooling, embs, dim) -> np.ndarray:
    stacked = np.stack([_track_vector(e["vectors"], dim) for e in embs])  # (n_tracks, dim)
    result = _pool(stacked, pooling)
    norm = np.linalg.norm(result)
    if norm > 0:
        result = result / norm
    conn.execute(
        """
        INSERT INTO artist_vectors
            (spec_hash, mbid, pooling, n_tracks, source_inputs, vector)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(spec_hash, mbid, pooling) DO UPDATE SET
            n_tracks = excluded.n_tracks,
            source_inputs = excluded.source_inputs,
            vector = excluded.vector
        """,
        (spec_hash, mbid, pooling, len(embs),
         json.dumps([e["input_hash"] for e in embs]), array_to_blob(result.astype(np.float32))),
    )
    return result


def _spec_dim(conn, spec_hash: str) -> int | None:
    row = conn.execute(
        "SELECT dim FROM embedding_specs WHERE spec_hash = ?", (spec_hash,)
    ).fetchone()
    return None if row is None else int(row["dim"])


def aggregate_artist_vector(
    conn,
    *,
    spec_hash: str,
    mbid: str,
    pooling: str = "mean",
) -> np.ndarray | None:
    """Pool all embeddings for one artist into a single L2-normalised vector.

    Audio windows are averaged per track first, then tracks are pooled.
    """
    if pooling not in POOLING_METHODS:
        raise ValueError(f"Unknown pooling method: {pooling}")
    dim = _spec_dim(conn, spec_hash)
    embs = get_artist_embeddings(conn, spec_hash, mbid)
    if dim is None or not embs:
        return None
    return _store_artist_vector(conn, spec_hash, mbid, pooling, embs, dim)


def rebuild_all_artist_vectors(
    conn,
    spec_hash: str,
    pooling: str = "mean",
) -> int:
    """Pool vectors for every canonical artist with embeddings. Returns count."""
    if pooling not in POOLING_METHODS:
        raise ValueError(f"Unknown pooling method: {pooling}")
    dim = _spec_dim(conn, spec_hash)
    if dim is None:
        return 0

    # Previously this LEFT JOINed recordings with no join key (artists x recordings),
    # which hung on real databases. Two keyed lookups instead.
    artists = conn.execute(
        """
        SELECT a.mbid FROM artists a
        WHERE a.is_canonical = 1 AND (
            EXISTS (SELECT 1 FROM embeddings e
                    WHERE e.spec_hash = ? AND e.entity_type = 'artist'
                      AND e.entity_id = a.mbid)
            OR EXISTS (SELECT 1 FROM recording_artists ra
                       JOIN audio_clips ac ON ac.track_id = ra.recording_id
                       JOIN embeddings e ON e.input_hash = ac.audio_sha256
                                        AND e.spec_hash = ?
                       WHERE ra.artist_id = a.id)
        )
        """,
        (spec_hash, spec_hash),
    ).fetchall()

    count = 0
    for row in artists:
        embs = get_artist_embeddings(conn, spec_hash, row["mbid"])
        if embs:
            _store_artist_vector(conn, spec_hash, row["mbid"], pooling, embs, dim)
            count += 1
    return count


# ------------------------------------------------------------------
# Query: artists with stored vectors
# ------------------------------------------------------------------
def get_artists_with_vectors(
    conn,
    spec_hash: str,
    pooling: str = "mean",
) -> list[dict[str, Any]]:
    """List artists with pooled vectors."""
    rows = conn.execute(
        """
        SELECT av.mbid, av.n_tracks, av.vector, av.source_inputs
        FROM artist_vectors av
        WHERE av.spec_hash = ? AND av.pooling = ?
        ORDER BY av.mbid
        """,
        (spec_hash, pooling),
    ).fetchall()
    return [
        {
            "mbid": row["mbid"],
            "n_tracks": row["n_tracks"],
            "vectors": blob_to_array(row["vector"]),
            "source_inputs": json.loads(row["source_inputs"]),
        }
        for row in rows
    ]
