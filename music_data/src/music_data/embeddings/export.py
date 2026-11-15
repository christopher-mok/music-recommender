"""Export artist embeddings as a float16 bundle.

Produces three files:
- ``vectors.f16.npy``  — float16 array of shape (N, dim)
- ``ids.json``         — list of MBIDs in the same order
- ``manifest.json``    — spec JSON, spec_hash, count, created_at
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from music_data.embeddings.helpers import get_artists_with_vectors

logger = logging.getLogger(__name__)


def export_artists(
    conn,
    spec_hash: str,
    output_dir: Path,
    pooling: str = "mean",
) -> Path:
    """Export artist vectors to *output_dir*.

    Returns the path to the created ``vectors.f16.npy`` file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    artists = get_artists_with_vectors(conn, spec_hash, pooling)

    if not artists:
        raise ValueError(
            f"No artist vectors found for spec={spec_hash}, pooling={pooling}"
        )

    mbids: list[str] = []
    sample_dim = artists[0]["vectors"].shape[-1]
    vectors: np.ndarray = np.zeros((len(artists), sample_dim), dtype=np.float32)
    for i, a in enumerate(artists):
        mbids.append(a["mbid"])
        v = a["vectors"]
        # Handle both 1D (dim,) and 2D (1, dim) vectors
        if v.ndim == 1:
            vectors[i] = v
        else:
            vectors[i] = v.flatten()

    # Convert to float16 for compactness
    vectors_f16 = vectors.astype(np.float16)

    vectors_path = output_dir / "vectors.f16.npy"
    np.save(str(vectors_path), vectors_f16)

    ids_path = output_dir / "ids.json"
    ids_path.write_text(json.dumps(mbids, indent=2), encoding="utf-8")

    manifest_path = output_dir / "manifest.json"
    spec_row = conn.execute(
        "SELECT spec_json, model_name FROM embedding_specs WHERE spec_hash = ?",
        (spec_hash,),
    ).fetchone()
    spec_json = json.loads(spec_row["spec_json"]) if spec_row else {}

    manifest = {
        "spec_hash": spec_hash,
        "model_name": spec_row["model_name"] if spec_row else None,
        "spec": spec_json,
        "pooling": pooling,
        "count": len(artists),
        "dim": int(vectors.shape[1]),
        "dtype": "float16",
        "created_at": _utc_now(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Exported %d artist vectors → %s", len(artists), output_dir)
    return vectors_path


def export_tracks(conn, spec_hash: str, output_dir: Path) -> Path:
    """Export one vector per audio clip (windows averaged, L2-normalised).

    ``ids.json`` holds one object per row: recording_id, recording_mbid,
    title, artist_mbid (first credited artist) and artist_name.
    """
    from music_data.embeddings.blob_helpers import blob_to_array

    output_dir.mkdir(parents=True, exist_ok=True)
    spec_row = conn.execute(
        "SELECT spec_json, model_name, dim, modality FROM embedding_specs WHERE spec_hash = ?",
        (spec_hash,),
    ).fetchone()
    if spec_row is None:
        raise ValueError(f"Unknown spec_hash {spec_hash}")
    if spec_row["modality"] != "audio":
        raise ValueError("Track export needs an audio spec; text specs are artist-level only")
    dim = int(spec_row["dim"])

    rows = conn.execute(
        """
        SELECT e.vectors, r.id AS recording_id, r.mbid AS recording_mbid, r.title,
               (SELECT a.mbid FROM recording_artists ra JOIN artists a ON a.id = ra.artist_id
                WHERE ra.recording_id = r.id ORDER BY ra.credit_order LIMIT 1) AS artist_mbid,
               (SELECT a.name FROM recording_artists ra JOIN artists a ON a.id = ra.artist_id
                WHERE ra.recording_id = r.id ORDER BY ra.credit_order LIMIT 1) AS artist_name
        FROM embeddings e
        JOIN audio_clips ac ON ac.audio_sha256 = e.input_hash
        JOIN recordings r ON r.id = ac.track_id
        WHERE e.spec_hash = ?
        ORDER BY r.id
        """,
        (spec_hash,),
    ).fetchall()
    if not rows:
        raise ValueError(f"No track embeddings found for spec={spec_hash}")

    vectors = np.zeros((len(rows), dim), dtype=np.float32)
    ids: list[dict] = []
    for i, row in enumerate(rows):
        v = blob_to_array(row["vectors"]).reshape(-1, dim).mean(axis=0)
        n = np.linalg.norm(v)
        vectors[i] = v / n if n > 0 else v
        ids.append({k: row[k] for k in
                    ("recording_id", "recording_mbid", "title", "artist_mbid", "artist_name")})

    vectors_path = output_dir / "vectors.f16.npy"
    np.save(str(vectors_path), vectors.astype(np.float16))
    (output_dir / "ids.json").write_text(json.dumps(ids, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    manifest = {
        "spec_hash": spec_hash,
        "model_name": spec_row["model_name"],
        "spec": json.loads(spec_row["spec_json"]),
        "level": "track",
        "pooling": "mean-over-windows",
        "count": len(rows),
        "dim": dim,
        "dtype": "float16",
        "created_at": _utc_now(),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Exported %d track vectors → %s", len(rows), output_dir)
    return vectors_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
