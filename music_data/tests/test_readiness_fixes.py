"""Tests for migration renumbering and track-level export."""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from music_data.database import PACKAGE_MIGRATIONS_DIR, get_connection, init_db
from music_data.embeddings.blob_helpers import array_to_blob
from music_data.embeddings.export import export_tracks


def test_renamed_migrations_not_reapplied(tmp_path):
    """A DB that applied migrations under their old names must not re-run them."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "migration_file TEXT UNIQUE NOT NULL, applied_at TEXT DEFAULT (datetime('now')))")
    old_order = ["001_initial_schema.sql", ("002_embeddings.sql", "004_embeddings.sql"),
                 "002_seed_selection_unique.sql", ("003_audio_clip_paths.sql", "005_audio_clip_paths.sql"),
                 "003_gender_and_genre_view.sql", ("004_preview_misses.sql", "006_preview_misses.sql")]
    for item in old_order:
        old, new = item if isinstance(item, tuple) else (item, item)
        conn.executescript((PACKAGE_MIGRATIONS_DIR / new).read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_migrations (migration_file) VALUES (?)", (old,))
    conn.commit()
    conn.close()

    init_db(db)  # would raise "duplicate column" if ALTER TABLEs re-ran
    names = {r[0] for r in sqlite3.connect(db).execute("SELECT migration_file FROM schema_migrations")}
    assert names == {p.name for p in PACKAGE_MIGRATIONS_DIR.glob("*.sql")}


def test_export_tracks(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_connection(db) as conn:
        conn.execute("INSERT INTO artists (id, mbid, name, is_canonical) VALUES (1, 'a1', 'A', 1)")
        conn.execute("INSERT INTO embedding_specs (spec_hash, model_name, modality, dim, spec_json) "
                     "VALUES ('s', 'audio-fake', 'audio', 4, '{}')")
        for rid in (10, 11):
            conn.execute("INSERT INTO recordings (id, mbid, title) VALUES (?, ?, ?)",
                         (rid, f"r{rid}", f"Song {rid}"))
            conn.execute("INSERT INTO recording_artists (recording_id, artist_id) VALUES (?, 1)", (rid,))
            conn.execute("INSERT INTO audio_clips (audio_sha256, track_id) VALUES (?, ?)", (f"h{rid}", rid))
            vecs = np.array([[3, 0, 0, 0], [3, 0, 0, 0]], dtype=np.float32) * rid
            conn.execute("INSERT INTO embeddings (spec_hash, input_hash, entity_type, entity_id, "
                         "n_windows, vectors) VALUES ('s', ?, 'track', ?, 2, ?)",
                         (f"h{rid}", str(rid), array_to_blob(vecs)))
        export_tracks(conn, "s", tmp_path / "out")

    X = np.load(tmp_path / "out" / "vectors.f16.npy")
    ids = json.loads((tmp_path / "out" / "ids.json").read_text(encoding="utf-8"))
    assert X.shape == (2, 4)
    np.testing.assert_allclose(X[:, 0], 1.0)
    assert [i["recording_id"] for i in ids] == [10, 11]
    assert ids[0]["artist_mbid"] == "a1"
