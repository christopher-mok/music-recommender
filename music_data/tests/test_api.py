"""Tests for the read-only API and the published bundle.

Publishes a bundle from data/music_metadata_100.db into a temp dir (about 2s).
Skipped when the pipeline DB is not present.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from music_data.api import Mulberry32, MusicData, match_score, normalize_name

ROOT = Path(__file__).resolve().parents[1]
SRC_DB = ROOT / "data" / "music_metadata_100.db"
UNKNOWN = "00000000-0000-0000-0000-000000000000"


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> Path:
    if not SRC_DB.exists():
        pytest.skip("pipeline DB not present")
    from music_data.publish import publish

    out = tmp_path_factory.mktemp("bundle")
    publish(SRC_DB, out)
    return out


@pytest.fixture(scope="module")
def md(bundle) -> MusicData:
    m = MusicData(bundle)
    yield m
    m.close()


@pytest.fixture(scope="module")
def ids(bundle) -> dict[str, str]:
    db = sqlite3.connect(bundle / "music.db")
    one = lambda sql: db.execute(sql).fetchone()[0]
    return {
        "top": one("SELECT mbid FROM artists WHERE in_catalog=1 ORDER BY popularity DESC LIMIT 1"),
        "no_audio": one("SELECT mbid FROM artists WHERE in_catalog=1 AND has_audio=0 LIMIT 1"),
        "no_genre": one("SELECT mbid FROM artists WHERE in_catalog=1 AND has_genre=0 LIMIT 1"),
        "stub": one("SELECT mbid FROM artists WHERE in_catalog=0 LIMIT 1"),
    }


# ---------------- bundle shape ----------------

def test_bundle_files(bundle):
    for f in ["music.db", "manifest.json", "audio.f16.npy", "text.f16.npy", "tracks.f16.npy",
              "audio_ids.json", "text_ids.json", "track_ids.json"]:
        assert (bundle / f).exists(), f


def test_vectors_match_ids_and_are_normalised(bundle):
    for sig, id_file in (("audio", "audio_ids"), ("text", "text_ids"), ("tracks", "track_ids")):
        mat = np.load(bundle / f"{sig}.f16.npy")
        assert mat.dtype == np.float16
        ids = json.loads((bundle / f"{id_file}.json").read_text(encoding="utf-8"))
        assert mat.shape[0] == len(ids)
        norms = np.linalg.norm(mat.astype(np.float32), axis=1)
        assert np.allclose(norms, 1.0, atol=2e-3)


def test_track_ids_line_up_with_tracks_table(bundle):
    db = sqlite3.connect(bundle / "music.db")
    for t in json.loads((bundle / "track_ids.json").read_text(encoding="utf-8")):
        row = db.execute("SELECT title FROM tracks WHERE id=?", (t["recording_id"],)).fetchone()
        assert row is not None and row[0] == t["title"]


def test_no_private_columns(bundle):
    db = sqlite3.connect(bundle / "music.db")
    cols = {c[1] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for c in db.execute(f"PRAGMA table_info({t[0]})")}
    assert not cols & {"preview_url", "local_path", "vector", "vectors"}


def test_stubs_are_slim(bundle):
    db = sqlite3.connect(bundle / "music.db")
    assert db.execute("SELECT COUNT(*) FROM artists WHERE in_catalog=0 AND "
                      "(country IS NOT NULL OR popularity IS NOT NULL)").fetchone()[0] == 0


def test_dataset_info(md):
    info = md.dataset_info()
    assert info["catalog_artists"] >= 1000
    assert info["bundle_version"]


# ---------------- api ----------------

def test_no_pipeline_imports():
    code = "import sys, music_data.api; bad=[m for m in sys.modules if m.startswith(('music_data.embeddings','music_data.fetchers','httpx','torch'))]; print(bad)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"


def test_normalize_and_match():
    assert normalize_name("  Björk!! ") == "bjork"
    assert match_score("beatles", "the beatles") == 0.8
    assert match_score("radiohed", "radiohead") > 0
    assert match_score("xyz", "radiohead") == 0


def test_mulberry32_known_values():
    r = Mulberry32(42)
    assert [round(r.next(), 9) for _ in range(3)] == [0.601103752, 0.448290559, 0.852465793]


def test_search(md):
    r = md.search_artists("radiohead", limit=5)
    assert r[0].name == "Radiohead" and r[0].in_catalog and r[0].score == 1.0
    assert md.search_artists("Radiohed")[0].name == "Radiohead"
    assert md.search_artists("bjork")[0].name == "Björk"
    assert md.search_artists("") == []
    res = md.search_artists("the", limit=50)
    flags = [x.in_catalog for x in res]
    assert flags == sorted(flags, reverse=True)
    assert all(x.in_catalog for x in md.search_artists("the", include_uncatalogued=False))


def test_get_artist(md, ids):
    a = md.get_artist(ids["top"])
    assert a.in_catalog and a.popularity is not None and 0 < a.popularity_percentile <= 1
    s = md.get_artist(ids["stub"])
    assert s.in_catalog is False and s.country is None and s.genres == ()
    assert md.get_artist(UNKNOWN) is None
    assert md.get_artist(None) is None  # type: ignore[arg-type]
    assert [x is None for x in md.get_artists([ids["top"], UNKNOWN])] == [False, True]
    json.dumps(a.to_dict())


def test_tracks(md, ids):
    tracks = md.get_artist_tracks(ids["top"])
    assert tracks and all(t.title for t in tracks)
    assert md.get_artist_tracks(ids["stub"]) == []


@pytest.mark.parametrize("signal", ["listeners", "audio", "text", "genre"])
def test_similar(md, ids, signal):
    res = md.similar_artists(ids["top"], signal, limit=10)
    assert 0 < len(res) <= 10
    assert all(r.signal == signal and r.mbid != ids["top"] for r in res)
    assert [r.score for r in res] == sorted((r.score for r in res), reverse=True)
    assert all(md.get_artist(r.mbid).in_catalog for r in res)


def test_missing_signals(md, ids):
    assert md.similar_artists(ids["no_audio"], "audio") == []
    assert md.get_vector(ids["no_audio"], "audio") is None
    assert md.similar_artists(ids["no_genre"], "genre") == []
    assert md.similar_artists(ids["stub"], "audio") == []
    assert md.similar_artists(ids["top"], "bogus") == []
    s = md.similarity_between(ids["top"], ids["no_audio"])
    assert s.scores["audio"] is None
    assert md.similarity_between(ids["top"], ids["stub"]) is None


def test_vectors(md, ids):
    v = md.get_vector(ids["top"], "audio")
    assert v.shape == (512,)
    res = md.nearest_to_vector(v, "audio", limit=3)
    assert res[0].mbid == ids["top"] and res[0].score > 0.99
    assert md.nearest_to_vector(v, "audio", exclude=[ids["top"]])[0].mbid != ids["top"]
    assert md.nearest_to_vector([1.0, 2.0], "audio") == []
    assert md.nearest_to_vector(v, "genre") == []


def test_genres_and_random(md):
    genres = md.list_genres()
    assert genres and genres[0].artist_count >= genres[-1].artist_count
    by = md.artists_by_genre(genres[0].genre, 5)
    assert len(by) == 5
    assert md.artists_by_genre("not a genre") == []
    a = [x.mbid for x in md.random_artists(5, seed=1)]
    assert a == [x.mbid for x in md.random_artists(5, seed=1)] and len(set(a)) == 5
    assert md.random_artists(3, genre="not a genre", seed=1) == []


def test_resolve(md):
    r = md.resolve_artist("RADIOHEAD")
    assert r and r.name == "Radiohead" and r.confidence == 1.0
    assert md.resolve_artist("zzzzqqqqxx") is None


def test_preview_url_bad_input(md):
    assert md.get_preview_url(-1) is None
    assert md.get_preview_url("abc") is None  # type: ignore[arg-type]
