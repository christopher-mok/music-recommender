"""Offline integration tests against current provider response shapes."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from music_data.cache import HttpCache
from music_data.collectors.artist_collector import _is_usable_studio_title
from music_data.collectors.pipeline import Pipeline
from music_data.config import Config
from music_data.database import get_connection, init_db
from music_data.fetchers.base import parse_retry_after
from music_data.fetchers.listenbrainz import ListenBrainzFetcher
from music_data.fetchers.musicbrainz import (
    AmbiguousArtistError,
    MusicBrainzFetcher,
    normalize_name,
)
from music_data.models import ArtistSeed
from music_data.reports.generator import generate_json_report, validate_database

MBID = "a74b1b7f-71a5-4011-9441-d0b5e4122711"
TARGET = "5b11f4ce-a62d-471e-81fc-a69a8278c7da"
MB = "https://musicbrainz.org/ws/2"
LB = "https://api.listenbrainz.org"
LABS = "https://labs.api.listenbrainz.org"


class Router(httpx.MockTransport):
    """Exact URL test router with simple request counting."""

    def __init__(self, handler):
        super().__init__(handler)


def config(tmp_path: Path, transport: httpx.BaseTransport) -> Config:
    cfg = Config.from_dict({
        "database": {"path": str(tmp_path / "test.sqlite")},
        "http": {"timeout": 2, "max_retries": 0},
        "cache": {"enabled": False},
        "collect": {"artists": [], "tracks_per_artist": 2},
        "musicbrainz": {"enabled": True},
        "listenbrainz": {"enabled": True, "similar_limit": 5},
    })
    cfg._transport = transport
    return cfg


def make_handler(requests: list[httpx.Request]):
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if request.url.host == "musicbrainz.org" and path.endswith("/artist"):
            return httpx.Response(200, json={"artists": [{
                "id": MBID, "name": "Radiohead", "country": "GB", "score": 100,
                "disambiguation": "English rock band", "type": "Group",
            }]}, request=request)
        if request.url.host == "musicbrainz.org" and path.endswith(f"/artist/{MBID}"):
            return httpx.Response(200, json={
                "id": MBID, "name": "Radiohead", "sort-name": "Radiohead",
                "country": "GB", "type": "Group", "area": {"name": "United Kingdom"},
                "life-span": {"begin": "1985", "ended": False},
                "aliases": [{"name": "Radiohead"}],
                "genres": [{"name": "alternative rock", "count": 5}],
                "tags": [{"name": "britpop", "count": 2}],
                "release-groups": [],
            }, request=request)
        if request.url.host == "api.listenbrainz.org" and path.endswith("/top-recordings-for-artist/" + MBID):
            return httpx.Response(200, json=[
                {"recording_mbid": "rec-1", "recording_name": "Creep", "length": 238000,
                 "artist_mbids": [MBID], "total_listen_count": 100, "total_user_count": 10},
                {"recording_mbid": "rec-2", "recording_name": "Karma Police", "length": 264000,
                 "artist_mbids": [MBID], "total_listen_count": 90, "total_user_count": 9},
            ], request=request)
        if request.url.host == "musicbrainz.org" and path.endswith("/recording/rec-1"):
            return httpx.Response(200, json={
                "id": "rec-1", "title": "Creep", "length": 238000,
                "artist-credit": [{"name": "Radiohead", "artist": {"id": MBID, "name": "Radiohead"}}],
                "first-release-date": "1992-09-21",
                "releases": [{"id": "rel-1", "title": "Pablo Honey", "date": "1993-02-22",
                              "country": "GB", "release-group": {"id": "rg-1", "primary-type": "Album"}}],
            }, request=request)
        if request.url.host == "musicbrainz.org" and path.endswith("/recording/rec-2"):
            return httpx.Response(404, json={"error": "not found"}, request=request)
        if request.url.host == "labs.api.listenbrainz.org":
            return httpx.Response(200, json=[{
                "artist_mbid": TARGET, "name": "Nirvana", "comment": "grunge band", "type": "Group", "score": 11156,
            }, {"artist_mbid": None, "name": "Unknown artist", "comment": "no MBID", "score": 12}], request=request)
        return httpx.Response(404, json={"error": f"unmocked {request.url}"}, request=request)
    return handle


def test_musicbrainz_identity_and_json_lists():
    requests: list[httpx.Request] = []
    fetcher = MusicBrainzFetcher(user_agent="test/1.0 (me@example.com)", max_retries=0,
                                 transport=Router(make_handler(requests)), use_cache=False)
    match = fetcher.resolve_artist("Radiohead", country="GB")
    metadata = fetcher.lookup_artist(match.mbid)
    assert match.mbid == MBID
    assert isinstance(metadata["genres"], list)
    assert isinstance(metadata["tags"], list)
    assert "aliases" in requests[-1].url.params["inc"]
    fetcher.close()


def test_ambiguous_name_not_merged():
    def handle(request):
        return httpx.Response(200, json={"artists": [
            {"id": "one", "name": "Test", "country": "US"},
            {"id": "two", "name": "Test", "country": "GB"},
        ]}, request=request)

    fetcher = MusicBrainzFetcher(user_agent="test", max_retries=0,
                                 transport=Router(handle), use_cache=False)
    with pytest.raises(AmbiguousArtistError):
        fetcher.resolve_artist("Test")
    assert fetcher.resolve_artist("Test", country="GB").mbid == "two"
    fetcher.close()


def test_listenbrainz_similarity_parser():
    def handle(request):
        assert request.method == "POST"
        return httpx.Response(200, json=[{
            "artist_mbid": TARGET, "name": "Nirvana", "score": 123,
            "comment": "US grunge band", "type": "Group",
        }], request=request)

    fetcher = ListenBrainzFetcher(algorithm="algo", max_retries=0, use_cache=False,
                                  transport=Router(handle))
    similar = fetcher.similar_artists(MBID)
    assert similar[0].mbid == TARGET
    assert similar[0].score == 123
    fetcher.close()


def test_recordings_relationships_resumability_and_integrity(tmp_path):
    requests: list[httpx.Request] = []
    transport = Router(make_handler(requests))
    cfg = config(tmp_path, transport)
    # Exercise the primary ListenBrainz popularity path in this fixture.
    cfg.listenbrainz.user_token = "test-token"
    init_db(cfg.database.path)
    seed = ArtistSeed("Radiohead", country="GB", reason="fixture", genres=("alternative rock",))
    pipeline = Pipeline(cfg, transport=transport)

    first = pipeline.run([seed])
    assert first["completed"] == 1
    assert first["results"][0]["recordings_count"] == 2
    assert any(
        "releases" in request.url.params.get("inc", "")
        for request in requests
        if request.url.path.endswith("/recording/rec-1")
    )
    with get_connection(cfg.database.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM recording_artists").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM recording_releases").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM artist_similarities").fetchone()[0] == 2
        target = conn.execute("SELECT mbid, is_canonical FROM artists WHERE name = 'Nirvana'").fetchone()
        assert tuple(target) == (TARGET, 0)
        assert validate_database(conn)["ok"]
        report = json.loads(generate_json_report(conn))
        assert report["validation"]["coverage"]["canonical_artists"] == 1

    # Simulate interruption after this artist's transaction committed.
    with get_connection(cfg.database.path) as conn:
        conn.execute("UPDATE ingestion_jobs SET status = 'partial', finished_at = NULL")
    cfg.collect.resume = True
    second = pipeline.run([ArtistSeed("Radiohead", mbid=MBID, country="GB", reason="resolved")])
    assert second["completed"] == 0
    with get_connection(cfg.database.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM ingestion_jobs").fetchone()[0] == 1


def test_fallback_title_filter_rejects_non_studio_material():
    assert _is_usable_studio_title("Paranoid Android")
    assert not _is_usable_studio_title("[untitled]")
    assert not _is_usable_studio_title("(interview with the band)")
    assert not _is_usable_studio_title("Creep (live at the Astoria)")
    assert not _is_usable_studio_title("Everything in Its Right Place (remix)")
    assert not _is_usable_studio_title("x" * 100)


def test_cache_and_retry_after(tmp_path):
    cache = HttpCache(tmp_path / "cache")
    fetcher = ListenBrainzFetcher(algorithm="algo", cache=cache, max_retries=0)
    key = "abc"
    fetcher.cache.put(key, url="https://example.com", status_code=200, payload={"ok": True})
    assert fetcher.cache.get(key)["payload"] == {"ok": True}
    assert parse_retry_after("3") == 3
    assert parse_retry_after("not a date", default=8) == 8
    fetcher.close()


def test_popularity_auth_required_falls_back_to_musicbrainz_browse(tmp_path):
    def handle(request):
        if request.url.host == "musicbrainz.org" and request.url.path.endswith(f"/artist/{MBID}"):
            return httpx.Response(200, json={
                "id": MBID, "name": "Radiohead", "country": "GB", "type": "Group",
                "area": {}, "life-span": {}, "aliases": [], "genres": [], "tags": [],
            }, request=request)
        if request.url.host == "api.listenbrainz.org":
            return httpx.Response(401, json={"error": "auth token required"}, request=request)
        if request.url.host == "musicbrainz.org" and request.url.path.endswith("/recording"):
            return httpx.Response(200, json={"recordings": [
                {"id": "browse-1", "title": "Song A", "length": 200000,
                 "artist-credit": [{"name": "Radiohead", "artist": {"id": MBID, "name": "Radiohead"}}]},
                {"id": "browse-2", "title": "Song A", "length": 201000,
                 "artist-credit": [{"name": "Radiohead", "artist": {"id": MBID, "name": "Radiohead"}}]},
                {"id": "browse-3", "title": "Song B", "length": 210000,
                 "artist-credit": [{"name": "Radiohead", "artist": {"id": MBID, "name": "Radiohead"}}]},
            ]}, request=request)
        if request.url.host == "labs.api.listenbrainz.org":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(404, json={"error": "unmocked"}, request=request)

    cfg = config(tmp_path, Router(handle))
    cfg.collect.tracks_per_artist = 2
    init_db(cfg.database.path)
    summary = Pipeline(cfg, transport=Router(handle)).run([
        ArtistSeed("Radiohead", mbid=MBID, country="GB")
    ])
    assert summary["results"][0]["recordings_count"] == 2
    with get_connection(cfg.database.path) as conn:
        titles = [row[0] for row in conn.execute("SELECT title FROM recordings ORDER BY title")]
        assert titles == ["Song A", "Song B"]


def test_popular_artist_seeds_skip_missing_mbid_and_page():
    pages = {
        0: [
            {"artist_mbid": MBID, "artist_name": "Radiohead", "listen_count": 10},
            {"artist_mbid": None, "artist_name": "A & B", "listen_count": 9},
        ],
        2: [{"artist_mbid": TARGET, "artist_name": "Nirvana", "listen_count": 8}],
    }

    def handle(request):
        assert request.url.path == "/1/stats/sitewide/artists"
        assert request.url.params["range"] == "this_year"
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"payload": {"artists": pages.get(offset, [])}},
                              request=request)

    fetcher = ListenBrainzFetcher(algorithm="algo", max_retries=0, use_cache=False,
                                  transport=Router(handle))
    seeds = fetcher.popular_artist_seeds(count=2, range_="this_year")
    fetcher.close()
    assert [s.mbid for s in seeds] == [MBID, TARGET]
    assert "#3" in seeds[1].reason


def test_merge_seeds_dedupes_by_mbid_and_name():
    from music_data.cli import merge_seeds

    base = [ArtistSeed(name="Radiohead", mbid=MBID), ArtistSeed(name="Björk")]
    extra = [ArtistSeed(name="Radiohead", mbid=MBID), ArtistSeed(name="björk", mbid=TARGET),
             ArtistSeed(name="SZA", mbid="x")]
    merged = merge_seeds(base, extra)
    assert [s.name for s in merged] == ["Radiohead", "Björk", "SZA"]
    assert merged[1].mbid == TARGET  # name-only seed adopts the popular MBID


def _search_fetcher(artists):
    def handle(request):
        return httpx.Response(200, json={"artists": artists}, request=request)
    return MusicBrainzFetcher(user_agent="t/1 (t@example.com)", max_retries=0, use_cache=False,
                              rate_limit=0, transport=Router(handle))


def test_country_hint_does_not_discard_sole_exact_match():
    f = _search_fetcher([{"id": TARGET, "name": "FKA twigs", "country": None, "score": 100}])
    assert f.resolve_artist("FKA twigs", country="GB").mbid == TARGET
    f.close()


def test_country_hint_still_disambiguates():
    f = _search_fetcher([
        {"id": TARGET, "name": "Rihanna", "country": "BB", "score": 100},
        {"id": MBID, "name": "Rihanna", "country": None, "score": 100},
    ])
    assert f.resolve_artist("Rihanna", country="BB").mbid == TARGET
    f.close()


def test_alias_match_for_renamed_artist():
    f = _search_fetcher([
        {"id": TARGET, "name": "Ye", "score": 100, "aliases": [{"name": "Kanye West"}]},
        {"id": MBID, "name": "Kanye West Tribute Band", "score": 80},
    ])
    assert f.resolve_artist("Kanye West", country="US").mbid == TARGET
    f.close()
