"""Pytest fixtures for the music data pipeline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

import httpx
import pytest


class _MockTransport(httpx.BaseTransport):
    """httpx transport that delegates to a dict of mocked responses."""

    def __init__(self) -> None:
        self._rules: list[dict[str, Any]] = []

    def add(self, method: str, url: str, json: Any = None, status: int = 200, body: bytes | None = None):
        """Register a mock response."""
        self._rules.append({
            "method": method.upper(),
            "url": url,
            "json": json,
            "status": status,
            "body": body,
        })

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        # Find matching rule
        for rule in self._rules:
            # Check method
            if rule["method"] != request.method.upper():
                continue

            # Check URL - allow exact match or prefix match
            req_url = str(request.url).split("?")[0]
            rule_url = rule["url"].split("?")[0]

            if req_url == rule_url or req_url.startswith(rule_url):
                # Build response
                headers = {"Content-Type": "application/json"}
                if rule.get("body"):
                    content = rule["body"]
                elif rule.get("json") is not None:
                    content = json.dumps(rule["json"]).encode()
                else:
                    content = b""

                return httpx.Response(
                    status_code=rule["status"],
                    headers=headers,
                    content=content,
                    request=request,
                )

        # No match found - raise error
        raise httpx.HTTPStatusError(
            f"No mock for {request.method} {request.url}",
            request=request,
            response=None,
        )


@pytest.fixture()
def mock_httpx_transport():
    """Provide an httpx transport backed by mock rules."""
    transport = _MockTransport()
    yield transport


@pytest.fixture()
def temp_db():
    """Create a temporary in-memory SQLite database for testing."""
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()

    from music_data.database import init_db
    init_db(db.name)

    yield db.name

    Path(db.name).unlink(missing_ok=True)


@pytest.fixture()
def conn(temp_db):
    """Provide a database connection."""
    from music_data.database import get_connection
    with get_connection(temp_db) as c:
        yield c


@pytest.fixture()
def mock_musicbrainz_artist_data() -> dict[str, Any]:
    """Sample MusicBrainz artist JSON for Radiohead."""
    return {
        "id": "a1687b2c-74e8-47c7-8bb4-79d8c6c3e5e5",
        "name": "Radiohead",
        "sort_name": "Radiohead",
        "disambiguation": "English rock band",
        "type": "Group",
        "country": "GB",
        "area": {
            "id": "5533add9-9b5d-392c-b143-7893d1c23fdb",
            "name": "United Kingdom",
        },
        "life_span": {
            "begin": "1985",
            "end": None,
            "ended": False,
        },
        "aliases": [
            {"name": "Radiohead", "sort_name": "Radiohead"},
            {"name": "Radio Heade", "sort_name": "Radio Heade", "begin": "1985"},
        ],
        "tags": {
            "tag": [
                {"name": "alternative rock", "count": 150},
                {"name": "britpop", "count": 80},
                {"name": "art rock", "count": 60},
            ]
        },
        "release-groups": {
            "release-group": [
                {"title": "OK Computer", "type": "Album", "date": "1997-05-21", "id": "rg1"},
                {"title": "The Bends", "type": "Album", "date": "1995-03-13", "id": "rg2"},
                {"title": "Kid A", "type": "Album", "date": "2000-10-02", "id": "rg3"},
            ]
        },
        "url-rels": [],
        "recordings": {"count": 80},
    }


@pytest.fixture()
def mock_lastfm_artist_info() -> dict[str, Any]:
    """Sample Last.fm artist.getInfo response."""
    return {
        "name": "Radiohead",
        "mbid": "a1687b2c-74e8-47c7-8bb4-79d8c6c3e5e5",
        "surname": "",
        "firstname": "",
        "stats": {
            "listeners": "4500000",
            "playcount": "890000000",
        },
        "url": "https://www.last.fm/music/Radiohead",
        "image": [
            {"text": "https://last.fm/img/small", "size": "small"},
            {"text": "https://last.fm/img/large", "size": "large"},
        ],
        "similar": {"artist": []},
        "tags": {
            "tag": [
                {"name": "alternative rock", "count": "350"},
                {"name": "britpop", "count": "200"},
                {"name": "art rock", "count": "150"},
            ]
        },
        "bio": {
            "summary": "Radiohead are an English rock band.",
            "content": "<p>Radiohead formed in Abingdon, Oxfordshire in 1985.</p>",
            "language": "en",
        },
        "tracks": {
            "track": [
                {
                    "name": "Creep",
                    "mbid": "t1",
                    "duration": "238000",
                    "streamable": "1",
                    "playcount": "500000000",
                },
                {
                    "name": "Karma Police",
                    "mbid": "t2",
                    "duration": "264000",
                    "streamable": "1",
                    "playcount": "300000000",
                },
                {
                    "name": "No Surprises",
                    "mbid": "t3",
                    "duration": "228000",
                    "streamable": "0",
                    "playcount": "200000000",
                },
            ]
        },
    }


@pytest.fixture()
def mock_lastfm_similar_artists() -> dict[str, Any]:
    """Sample Last.fm artist.getSimilar response."""
    return {
        "similar": {
            "artist": [
                {
                    "name": "Thom Yorke",
                    "mbid": "s1",
                    "match": "0.85",
                    "id": "s1",
                    "url": "https://www.last.fm/music/Thom+Yorke",
                },
                {
                    "name": "Portishead",
                    "mbid": "s2",
                    "match": "0.72",
                    "id": "s2",
                    "url": "https://www.last.fm/music/Portishead",
                },
                {
                    "name": "Muse",
                    "mbid": "s3",
                    "match": "0.65",
                    "id": "s3",
                    "url": "https://www.last.fm/music/Muse",
                },
                {
                    "name": "Massive Attack",
                    "mbid": "s4",
                    "match": "0.60",
                    "id": "s4",
                    "url": "https://www.last.fm/music/Massive+Attack",
                },
                {
                    "name": "Sigur Rós",
                    "mbid": "s5",
                    "match": "0.55",
                    "id": "s5",
                    "url": "https://www.last.fm/music/Sigur+R%C3%B3s",
                },
            ]
        }
    }


@pytest.fixture()
def mock_musicbrainz_404():
    """Response for a non-existent artist."""
    return {
        "error": {
            "status": 404,
            "message": "No artist found with MBID: nonexistent-id",
            "keys": ["help", "error"],
        }
    }


@pytest.fixture()
def sample_config():
    """Create a minimal config for testing."""
    from music_data.config import Config
    return Config.from_dict({
        "collect": {
            "database_path": ":memory:",
            "concurrency": 1,
            "retry_max": 3,
            "retry_delay": 0.01,
            "lastfm_similar_limit": 5,
            "seed_artists": [],
        },
        "lastfm": {
            "api_key": "test_key_12345",
            "max_requests_per_second": 100.0,
        },
        "musicbrainz": {
            "user_agent": "test_pipeline/0.1 (test@example.com)",
        },
    })
