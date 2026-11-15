"""Tests for the preview top-up selection (artists short of clips)."""
from __future__ import annotations

from music_data.cli import artists_short_of_clips
from music_data.database import get_connection, init_db


def _artist(conn, name, mbid, status="complete"):
    return conn.execute(
        "INSERT INTO artists (name, mbid, is_canonical, collection_status) VALUES (?, ?, 1, ?)",
        (name, mbid, status)).lastrowid


def _track_with_clip(conn, artist_id, n, clip=True):
    rid = conn.execute("INSERT INTO recordings (mbid, title, is_canonical) VALUES (?, ?, 1)",
                       (f"rec-{artist_id}-{n}", f"Song {n}")).lastrowid
    conn.execute("INSERT INTO recording_artists (recording_id, artist_id, credit_order) "
                 "VALUES (?, ?, 0)", (rid, artist_id))
    if clip:
        conn.execute("INSERT INTO audio_clips (audio_sha256, track_id, provider) "
                     "VALUES (?, ?, 'deezer')", (f"sha-{rid}", rid))
    return rid


def test_artists_short_of_clips(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_connection(db) as conn:
        full = _artist(conn, "Full", "m1")
        short = _artist(conn, "Short", "m2")
        _artist(conn, "Stub", "m3", status="stub")
        for i in range(5):
            _track_with_clip(conn, full, i)
        for i in range(3):
            _track_with_clip(conn, short, i, clip=i < 2)

        result = artists_short_of_clips(conn, 5)

    assert [(a["name"], a["clips"]) for a in result] == [("Short", 2)]
