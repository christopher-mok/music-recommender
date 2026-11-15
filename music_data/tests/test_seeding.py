"""Tests for quota seeding, stub expansion, tag dedupe, gender and genre view."""

from __future__ import annotations

from music_data.collectors.artist_collector import ArtistCollector
from music_data.database import get_connection, init_db, upsert_artist, upsert_stub_artist
from music_data.seeding import QuotaSlot, genre_family, quota_seeds, stub_expansion_seeds


class FakeMB:
    def __init__(self, results):
        self.results = results

    def search_artists_query(self, query, limit=100):
        return self.results[query]


class FakeLB:
    def __init__(self, listeners):
        self.listeners = listeners

    def artist_popularity(self, mbids):
        return {m: {"listens": 0, "listeners": self.listeners[m]} for m in mbids if m in self.listeners}


def test_quota_seeds_rank_by_listeners_and_skip_taken():
    mb = FakeMB({
        "q1": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"}],
        "q2": [{"id": "b", "name": "B"}, {"id": "d", "name": "D"}, {"id": "z", "name": "Z"}],
    })
    lb = FakeLB({"a": 5, "b": 50, "c": 20, "d": 1})  # z has no listeners
    seeds = quota_seeds([QuotaSlot("s1", "q1", 2), QuotaSlot("s2", "q2", 2)], mb, lb,
                        exclude_mbids={"c"})
    assert [s.mbid for s in seeds] == ["b", "a", "d"]
    assert "Quota slot 's1'" in seeds[0].reason


def test_genre_family_specific_before_generic():
    assert genre_family("melodic dubstep") == "edm/dance"
    assert genre_family("synthwave") == "synthwave/retro"
    assert genre_family("k-pop") == "k/j-pop"
    assert genre_family("alternative metal") == "metal"
    assert genre_family("indie pop") == "pop"


def _canon(conn, mbid, name, genre, votes=10):
    aid = upsert_artist(conn, mbid=mbid, name=name, collection_status="complete")
    tid = conn.execute("INSERT INTO tags (name, normalized_name, tag_type) VALUES (?, ?, 'genre') "
                       "ON CONFLICT DO NOTHING RETURNING id", (genre, genre)).fetchone()
    tid = tid[0] if tid else conn.execute("SELECT id FROM tags WHERE normalized_name=?", (genre,)).fetchone()[0]
    conn.execute("INSERT INTO artist_tags (artist_id, tag_id, source, count) VALUES (?, ?, 'musicbrainz', ?)",
                 (aid, tid, votes))
    return aid


def _link(conn, src, dst, score=1.0):
    conn.execute("INSERT INTO artist_similarities (source_artist_id, target_artist_id, provider, score) "
                 "VALUES (?, ?, 'listenbrainz', ?)", (src, dst, score))


def test_stub_expansion_prefers_links_from_rare_genres(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_connection(db) as conn:
        rock = [_canon(conn, f"r{i}", f"Rock{i}", "rock") for i in range(6)]
        synth = [_canon(conn, f"s{i}", f"Synth{i}", "synthwave") for i in range(2)]
        rock_stub = upsert_stub_artist(conn, mbid="rs", name="RockStub")
        synth_stub = upsert_stub_artist(conn, mbid="ss", name="SynthStub")
        lone = upsert_stub_artist(conn, mbid="ls", name="Lone")
        for a in rock[:3]:
            _link(conn, a, rock_stub)
        for a in synth:
            _link(conn, a, synth_stub)
        _link(conn, synth[0], lone)  # only one link: below min_links
        conn.commit()
        seeds = stub_expansion_seeds(conn, 5)
    assert [s.mbid for s in seeds] == ["ss", "rs"]


def test_genre_view_drops_low_vote_noise_keeps_top(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    with get_connection(db) as conn:
        aid = _canon(conn, "k", "Kendrick", "hip hop", 18)
        _canon(conn, "x", "X", "house", 2)  # sole genre: kept as top genre
        tid = conn.execute("SELECT id FROM tags WHERE normalized_name='house'").fetchone()[0]
        conn.execute("INSERT INTO artist_tags (artist_id, tag_id, source, count) VALUES (?, ?, 'musicbrainz', 2)",
                     (aid, tid))
        conn.commit()
        rows = conn.execute("SELECT a.name, g.genre FROM artist_genres g JOIN artists a ON a.id=g.artist_id "
                            "ORDER BY 1, 2").fetchall()
    assert [tuple(r) for r in rows] == [("Kendrick", "hip hop"), ("X", "house")]


def test_metadata_tags_not_duplicated_and_gender_saved(tmp_path):
    db = tmp_path / "t.db"
    init_db(db)
    data = {"id": "m1", "name": "Solo", "type": "Person", "gender": "Female",
            "genres": [{"name": "Synthwave", "count": 4}],
            "tags": [{"name": "synthwave", "count": 4}, {"name": "80s", "count": 2}]}
    with get_connection(db) as conn:
        aid = ArtistCollector._save_artist(conn, data)
        n = ArtistCollector._save_metadata_tags(conn, aid, data)
        conn.commit()
        gender = conn.execute("SELECT gender FROM artists WHERE id=?", (aid,)).fetchone()[0]
    assert n == 2  # synthwave (genre) + 80s (descriptive), not synthwave twice
    assert gender == "female"


def test_ambiguous_tie_break_needs_clear_listener_winner():
    import pytest
    from music_data.fetchers.musicbrainz import AmbiguousArtistError

    collector = object.__new__(ArtistCollector)
    cands = [{"id": "k", "name": "JENNIE", "country": "KR"}, {"id": "f", "name": "Jennie", "country": "FI"}]
    collector.lb = FakeLB({"k": 50000, "f": 30})
    assert collector._break_tie_by_listeners(AmbiguousArtistError("JENNIE", cands)).mbid == "k"
    collector.lb = FakeLB({"k": 500, "f": 300})  # too close: stay ambiguous
    with pytest.raises(AmbiguousArtistError):
        collector._break_tie_by_listeners(AmbiguousArtistError("JENNIE", cands))
