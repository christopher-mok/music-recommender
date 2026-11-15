"""Regression tests for embedding pipeline bugs fixed on 2026-11-08.

Each test names the bug it guards against. No network, torch, or real models.
"""

from __future__ import annotations

import sys
import time
import types

import numpy as np
import pytest
from click.testing import CliRunner

from music_data.cli import cli, embed_clips, select_preview_tracks, store_clip
from music_data.database import get_connection, init_db
from music_data.embeddings import embedder_for
from music_data.embeddings.blob_helpers import blob_to_array
from music_data.embeddings.helpers import rebuild_all_artist_vectors, register_spec, upsert_embedding
from music_data.fetchers.previews import DeezerPreviewFetcher, _norm, pcm_sha256


# ── fixtures ────────────────────────────────────────────────────────

def _seed(conn, n_artists=2, tracks_per_artist=3):
    for a in range(1, n_artists + 1):
        conn.execute("INSERT INTO artists (id, mbid, name, is_canonical) VALUES (?, ?, ?, 1)",
                     (a, f"mbid-{a}", f"Artist {a}"))
        for t in range(tracks_per_artist):
            rid = a * 100 + t
            conn.execute("INSERT INTO recordings (id, mbid, title, duration_ms, is_canonical) "
                         "VALUES (?, ?, ?, 200000, 1)", (rid, f"rec-{rid}", f"Song {rid}"))
            conn.execute("INSERT INTO recording_artists (artist_id, recording_id) VALUES (?, ?)",
                         (a, rid))
            _popularity(conn, rid, t + 1)


def _popularity(conn, rid, rank):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(recording_popularity)")}
    values = {"recording_id": rid, "provider": "listenbrainz",
              "measure": "total_listen_count", "rank": rank, "value": 1000 - rank}
    values = {k: v for k, v in values.items() if k in cols}
    conn.execute(f"INSERT INTO recording_popularity ({', '.join(values)}) "
                 f"VALUES ({', '.join('?' * len(values))})", list(values.values()))


def _clip(rid, path=None):
    return {"audio_sha256": f"sha-{rid}", "track_id": rid, "mbid_track": f"rec-{rid}",
            "provider": "deezer", "provider_track_id": str(rid), "preview_url": "http://x",
            "duration_s": 30.0, "path": path}


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    with get_connection(path) as conn:
        _seed(conn)
    return path


class WindowEmbedder:
    """Fake audio model returning (n_windows, dim) per clip; can fail on chosen paths."""

    name, modality, dim = "win-fake", "audio", 4

    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.calls = []

    def spec(self):
        return {"model": "win-fake", "revision": "x", "dim": self.dim}

    def embed(self, paths):
        self.calls.append(list(paths))
        for p in paths:
            if p in self.fail_on:
                raise RuntimeError(f"cannot decode {p}")
        return [np.tile(np.eye(self.dim)[i % self.dim], (3, 1)) for i, _ in enumerate(paths)]


# ── aggregation ─────────────────────────────────────────────────────

def test_rebuild_does_not_cross_join(tmp_path):
    """Bug: LEFT JOIN recordings without a key made rebuild O(artists x recordings)."""
    path = str(tmp_path / "big.db")
    init_db(path)
    with get_connection(path) as conn:
        _seed(conn, n_artists=300, tracks_per_artist=20)
        model = embedder_for("fake")
        sh = register_spec(conn, embedder=model)
        for a in range(1, 301):
            upsert_embedding(conn, spec_hash=sh, input_hash=f"h{a}", entity_type="artist",
                             entity_id=f"mbid-{a}", vectors=model.embed([str(a)]))
        t0 = time.perf_counter()
        assert rebuild_all_artist_vectors(conn, sh) == 300
        assert time.perf_counter() - t0 < 10


def test_pooling_averages_windows_then_tracks(db):
    """Bug: per-window audio vectors were stacked unpooled, giving (windows, dim) artists."""
    with get_connection(db) as conn:
        model = WindowEmbedder()
        sh = register_spec(conn, embedder=model)
        for t, rid in enumerate((100, 101)):
            store_clip(conn, _clip(rid))
            wins = np.zeros((3, 4), np.float32)
            wins[:, t] = 1.0
            upsert_embedding(conn, spec_hash=sh, input_hash=f"sha-{rid}", entity_type="track",
                             entity_id=str(rid), vectors=wins)
        assert rebuild_all_artist_vectors(conn, sh) == 1
        vec = blob_to_array(conn.execute(
            "SELECT vector FROM artist_vectors WHERE mbid = 'mbid-1'").fetchone()[0])
    assert vec.shape == (4,)
    np.testing.assert_allclose(vec, np.array([1, 1, 0, 0]) / np.sqrt(2), atol=1e-6)


def test_aggregate_command_runs(db):
    """Bug: `embeddings aggregate` raised NameError (missing import)."""
    with get_connection(db) as conn:
        model = embedder_for("fake")
        sh = register_spec(conn, embedder=model)
        upsert_embedding(conn, spec_hash=sh, input_hash="h", entity_type="artist",
                         entity_id="mbid-1", vectors=model.embed(["x"]))
    res = CliRunner().invoke(cli, ["embeddings", "aggregate", "--db-path", db])
    assert res.exit_code == 0, res.output
    assert "Aggregated 1 artist" in res.output


# ── previews: resumability ─────────────────────────────────────────

def test_select_preview_tracks_skips_done_and_missed(db):
    """Bug: every run re-searched and re-downloaded every track."""
    with get_connection(db) as conn:
        store_clip(conn, _clip(100))
        conn.execute("INSERT INTO preview_misses (track_id, provider, reason) "
                     "VALUES (101, 'deezer', 'no_match')")
        ids = {t["track_id"] for t in select_preview_tracks(conn)}
        retry = {t["track_id"] for t in select_preview_tracks(conn, retry_misses=True)}
    assert 100 not in ids and 101 not in ids and 102 in ids
    assert 101 in retry and 100 not in retry


def test_select_preview_tracks_dedupes_collabs_and_uses_names(db):
    """Bug: artist MBID was sent as the search name; collabs were searched twice."""
    with get_connection(db) as conn:
        conn.execute("INSERT INTO recording_artists (artist_id, recording_id) VALUES (2, 100)")
        tracks = select_preview_tracks(conn)
    assert sum(t["track_id"] == 100 for t in tracks) == 1
    assert all(t["artist"].startswith("Artist ") for t in tracks)


def test_previews_command_records_misses_and_resumes(db, monkeypatch):
    calls = []

    def fake_find(self, tracks, **kw):
        calls.append(tracks[0]["track_id"])
        tid = tracks[0]["track_id"]
        if tid == 101:
            raise RuntimeError("network down")
        if tid == 200:  # same audio as track 100 (duplicate recording)
            return [{**_clip(200, path="/tmp/100.mp3"), "audio_sha256": "sha-100"}]
        return [] if tid == 102 else [_clip(tid, path=f"/tmp/{tid}.mp3")]

    monkeypatch.setattr(DeezerPreviewFetcher, "find_clips", fake_find)
    runner = CliRunner()
    res = runner.invoke(cli, ["embeddings", "previews", "--db-path", db])
    assert res.exit_code == 0, res.output
    assert "1 errors" in res.output
    with get_connection(db) as conn:
        reasons = dict(conn.execute("SELECT track_id, reason FROM preview_misses").fetchall())
    assert reasons[101] == "error" and reasons[102] == "no_match"
    assert reasons[200] == "duplicate_audio"

    calls.clear()
    res = runner.invoke(cli, ["embeddings", "previews", "--db-path", db])
    assert calls == [] and "No tracks need previews" in res.output


# ── previews: matching ─────────────────────────────────────────────

class _Resp:
    def __init__(self, data=None, content=b""):
        self._data, self.content = data, content

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _fetcher_with(items, monkeypatch, tmp_path):
    import music_data.fetchers.previews as pv
    monkeypatch.setattr(pv, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pv, "pcm_sha256", lambda b: "pcmhash")
    f = DeezerPreviewFetcher(rate_limit=1000)
    queries = []

    def fake_get(url, **kw):
        if "params" in kw:
            queries.append(kw["params"]["q"])
            return _Resp({"data": items})
        return _Resp(content=b"ID3fake")

    monkeypatch.setattr(f, "_get", fake_get)
    return f, queries


def _item(title, artist, duration_s, id_=1):
    return {"id": id_, "title": title, "title_short": title, "duration": duration_s,
            "preview": f"http://p/{id_}", "artist": {"name": artist}}


def test_match_uses_seconds_and_plain_query(monkeypatch, tmp_path):
    """Bugs: Deezer durations (seconds) were divided by 1000; artist:"..." filter is dead."""
    f, queries = _fetcher_with([_item("Fractals", "Seven Lions", 299)], monkeypatch, tmp_path)
    clips = f.find_clips([{"artist": "Seven Lions", "track": "Fractals", "duration": 299000,
                           "track_id": 1, "mbid_track": None}])
    assert len(clips) == 1 and clips[0]["audio_sha256"] == "pcmhash"
    assert queries == ["seven lions fractals"]


def test_match_rejects_wrong_artist_unless_duration_exact(monkeypatch, tmp_path):
    items = [_item("Rush Over Me", "Someone Else", 250, 1)]
    f, _ = _fetcher_with(items, monkeypatch, tmp_path)
    far = f.find_clips([{"artist": "Illenium", "track": "Rush Over Me", "duration": 245000,
                         "track_id": 1, "mbid_track": None}])
    near = f.find_clips([{"artist": "Illenium", "track": "Rush Over Me", "duration": 251000,
                          "track_id": 1, "mbid_track": None}])
    assert far == [] and len(near) == 1


def test_cache_filename_is_windows_safe(monkeypatch, tmp_path):
    """Bug: 'file:' hash prefix put a colon in the filename."""
    f, _ = _fetcher_with([_item("A", "B", 100)], monkeypatch, tmp_path)
    import music_data.fetchers.previews as pv
    monkeypatch.setattr(pv, "pcm_sha256", lambda b: "file:abc")
    clip = f.find_clips([{"artist": "B", "track": "A", "duration": 100000,
                          "track_id": 1, "mbid_track": None}])[0]
    assert ":" not in clip["path"].split("\\")[-1].split("/")[-1]


def test_norm():
    assert _norm("Where’d U Go") == _norm("whered u go")
    assert _norm("ILLENIUM") == _norm("Illenium")


def test_pcm_hash_falls_back_on_garbage():
    assert pcm_sha256(b"not audio").startswith("file:")


# ── embed: failure isolation ───────────────────────────────────────

def test_embed_clips_isolates_bad_clip(db, tmp_path):
    """Bug: one undecodable clip aborted the whole embed run."""
    paths = {}
    for rid in (100, 101, 102):
        p = tmp_path / f"{rid}.mp3"
        p.write_bytes(b"x")
        paths[rid] = str(p)
    with get_connection(db) as conn:
        for rid in (100, 101, 102):
            store_clip(conn, _clip(rid, paths[rid]))
        store_clip(conn, _clip(200, str(tmp_path / "missing.mp3")))
        model = WindowEmbedder(fail_on={paths[101]})
        sh = register_spec(conn, embedder=model)
        clips = conn.execute("SELECT audio_sha256, track_id, local_path FROM audio_clips "
                             "ORDER BY track_id").fetchall()
        stats = embed_clips(conn, model, sh, clips)
        done = {r[0] for r in conn.execute("SELECT input_hash FROM embeddings")}
        failed = {r[0] for r in conn.execute("SELECT input_hash FROM embedding_failures")}
    assert stats == {"ok": 2, "failed": 2}
    assert done == {"sha-100", "sha-102"}
    assert failed == {"sha-101", "sha-200"}


def test_embed_clips_clears_failure_on_success(db, tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    with get_connection(db) as conn:
        store_clip(conn, _clip(100, str(p)))
        model = WindowEmbedder()
        sh = register_spec(conn, embedder=model)
        conn.execute("INSERT INTO embedding_failures (spec_hash, input_hash, error) "
                     "VALUES (?, 'sha-100', 'old')", (sh,))
        clips = conn.execute("SELECT audio_sha256, track_id, local_path FROM audio_clips").fetchall()
        embed_clips(conn, model, sh, clips)
        assert conn.execute("SELECT COUNT(*) FROM embedding_failures").fetchone()[0] == 0


# ── models ──────────────────────────────────────────────────────────

def test_muq_loads_pinned_revision(monkeypatch):
    """Bug: the revision check always failed, so MuQ loaded `main`, not the spec's sha."""
    from music_data.embeddings import audio_models

    seen = {}

    class FakeMuQ:
        @classmethod
        def from_pretrained(cls, *args, **kwargs):
            seen.update(kwargs)
            return cls()

        def to(self, device):
            return self

        def eval(self):
            return self

    monkeypatch.setitem(sys.modules, "muq", types.SimpleNamespace(MuQMuLan=FakeMuQ))
    monkeypatch.setattr(audio_models, "_device", lambda: "cpu")
    monkeypatch.setattr(audio_models, "_resolve_revision", lambda repo, rev: "a" * 40)
    m = audio_models.MuQMuLanEmbedder()
    m._load()
    assert seen["revision"] == "a" * 40 == m.spec()["revision"]


def test_windows_cover_clip():
    from music_data.embeddings.audio_models import windows
    sr = 10
    w = windows(np.zeros(300, np.float32), sr, 10.0, 10.0)  # 30 s -> 3 windows
    assert len(w) == 3 and all(len(x) == 100 for x in w)
    assert len(windows(np.zeros(50, np.float32), sr, 10.0, 10.0)) == 1


# ── relaxed matching (retry of misses) ─────────────────────────────

def test_relaxed_accepts_same_artist_different_edit(monkeypatch, tmp_path):
    """MusicBrainz duration is the album cut; Deezer only has the 3:30 edit."""
    f, _ = _fetcher_with([_item("Talking Body", "Tove Lo", 210)], monkeypatch, tmp_path)
    clips = f.find_clips([{"artist": "Tove Lo", "track": "Talking Body", "duration": 238000,
                           "track_id": 1, "mbid_track": None}])
    assert len(clips) == 1


def test_relaxed_strips_feat_and_remaster(monkeypatch, tmp_path):
    f, _ = _fetcher_with([_item("Naima", "John Coltrane", 300)], monkeypatch, tmp_path)
    clips = f.find_clips([{"artist": "John Coltrane", "track": "Naima - Remastered 2020",
                           "duration": 262853, "track_id": 1, "mbid_track": None}])
    assert len(clips) == 1


def test_relaxed_still_requires_same_artist(monkeypatch, tmp_path):
    f, _ = _fetcher_with([_item("Talking Body", "A Cover Band", 210)], monkeypatch, tmp_path)
    clips = f.find_clips([{"artist": "Tove Lo", "track": "Talking Body", "duration": 238000,
                           "track_id": 1, "mbid_track": None}])
    assert clips == []


def test_non_latin_titles_do_not_match_everything(monkeypatch, tmp_path):
    """Bug: an all-Hangul title normalised to '' and equalled any other '' title."""
    f, _ = _fetcher_with([_item("사랑", "가수", 200)], monkeypatch, tmp_path)
    miss = f.find_clips([{"artist": "가수", "track": "이별", "duration": 200000,
                          "track_id": 1, "mbid_track": None}])
    hit = f.find_clips([{"artist": "가수", "track": "사랑", "duration": 200000,
                         "track_id": 1, "mbid_track": None}])
    assert miss == [] and len(hit) == 1


def test_core_title_keeps_real_words():
    from music_data.fetchers.previews import _core_title
    assert _core_title("Ric Flair Drip (feat. Offset)") == "Ric Flair Drip"
    assert _core_title("With or Without You") == "With or Without You"
    assert _core_title("Live Forever") == "Live Forever"
