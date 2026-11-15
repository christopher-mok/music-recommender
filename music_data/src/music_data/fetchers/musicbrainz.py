"""MusicBrainz WS/2 client.

Only verified include parameters are used. MusicBrainz returns lists for
``aliases``, ``tags``, ``genres``, and ``release-groups`` in JSON mode.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from music_data.fetchers.base import BaseFetcher, CollectionError
from music_data.models import ArtistMatch

logger = logging.getLogger(__name__)

MUSICBRAINZ_BASE = "https://musicbrainz.org/ws/2"

ARTIST_INCLUDES = "aliases+tags+genres+url-rels+release-groups"
# MusicBrainz browse does not accept ``releases``/``media`` for recordings, but
# a direct recording lookup does. Keep the two includes separate.
RECORDING_BROWSE_INCLUDES = "artist-credits+tags+genres"
RECORDING_LOOKUP_INCLUDES = "artist-credits+releases+media+tags+genres"


class ArtistNotFoundError(CollectionError):
    """No exact artist identity could be resolved."""


class AmbiguousArtistError(CollectionError):
    """Multiple plausible identities exist and no disambiguator was supplied."""

    def __init__(self, name: str, candidates: list[dict[str, Any]]) -> None:
        self.name = name
        self.candidates = candidates
        rendered = ", ".join(
            f"{c.get('name')} ({c.get('disambiguation') or 'no disambiguation'}, "
            f"country={c.get('country') or '?'}, score={c.get('score')})"
            for c in candidates[:5]
        )
        super().__init__(
            f"Ambiguous artist '{name}'; supply an MBID or country. Candidates: {rendered}"
        )


def normalize_name(name: str) -> str:
    """Normalize a name for exact, accent-insensitive comparison."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text)
    return " ".join(text.split())


class MusicBrainzFetcher(BaseFetcher):
    provider = "musicbrainz"

    def __init__(
        self,
        *,
        user_agent: str,
        rate_limit: float = 1.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_url=MUSICBRAINZ_BASE,
            user_agent=user_agent,
            rate_limit=rate_limit,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Raw API calls
    # ------------------------------------------------------------------
    def search_artists(self, name: str, limit: int = 10) -> list[dict[str, Any]]:
        escaped = str(name).replace("\\", "\\\\").replace('"', '\\"')
        payload = self.get(
            "artist",
            {
                "query": f'artist:"{escaped}"',
                "fmt": "json",
                "limit": limit,
            },
        )
        artists = payload.get("artists", []) if isinstance(payload, dict) else []
        logger.debug("MusicBrainz search '%s' returned %s candidates", name, len(artists))
        return artists

    def search_artists_query(self, query: str, limit: int = 100) -> list[dict[str, Any]]:
        """Run a raw Lucene artist query (e.g. 'tag:"synthwave" AND begin:[2010 TO *]')."""
        payload = self.get("artist", {"query": query, "fmt": "json", "limit": min(limit, 100)})
        artists = payload.get("artists", []) if isinstance(payload, dict) else []
        return artists if isinstance(artists, list) else []

    def lookup_artist(self, mbid: str) -> dict[str, Any]:
        payload = self.get(
            f"artist/{mbid}",
            {"fmt": "json", "inc": ARTIST_INCLUDES},
        )
        if not isinstance(payload, dict) or "id" not in payload:
            raise CollectionError(f"Unexpected MusicBrainz artist response for {mbid}")
        return payload

    def browse_recordings(
        self, artist_mbid: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        payload = self.get(
            "recording",
            {
                "artist": artist_mbid,
                "fmt": "json",
                "inc": RECORDING_BROWSE_INCLUDES,
                "limit": min(max(limit, 1), 100),
                "offset": max(offset, 0),
            },
        )
        recordings = payload.get("recordings", []) if isinstance(payload, dict) else []
        return recordings

    def search_recordings(self, artist_mbid: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """Search recordings credited to an artist.

        This is a relevance-ranked text search, not a popularity ranking. It is
        used only as a fallback when a popularity source is unavailable.
        """
        payload = self.get(
            "recording",
            {
                "query": f"arid:{artist_mbid}",
                "fmt": "json",
                "limit": min(max(limit, 1), 100),
            },
        )
        recordings = payload.get("recordings", []) if isinstance(payload, dict) else []
        return recordings

    def lookup_recording(self, mbid: str) -> dict[str, Any]:
        payload = self.get(
            f"recording/{mbid}",
            {"fmt": "json", "inc": RECORDING_LOOKUP_INCLUDES},
        )
        if not isinstance(payload, dict) or "id" not in payload:
            raise CollectionError(f"Unexpected MusicBrainz recording response for {mbid}")
        return payload

    # ------------------------------------------------------------------
    # Identity resolution
    # ------------------------------------------------------------------
    def resolve_artist(
        self,
        name: str,
        *,
        country: str | None = None,
        mbid: str | None = None,
    ) -> ArtistMatch:
        """Resolve a name to exactly one MusicBrainz identity.

        The resolver never picks between multiple same-name artists silently.
        An explicit MBID is always authoritative.
        """
        if mbid:
            data = self.lookup_artist(mbid)
            return _match_from_payload(data, score=100)

        wanted = normalize_name(name)
        candidates = self.search_artists(name, limit=10)
        exact = [c for c in candidates if normalize_name(c.get("name", "")) == wanted]
        if not exact:
            # Renamed artists (e.g. "Kanye West" -> "Ye") match via an alias.
            exact = [
                c for c in candidates
                if any(normalize_name(a.get("name", "")) == wanted for a in c.get("aliases") or [])
            ]

        if country and exact:
            # Country is a disambiguation hint: narrow with it when it matches
            # something, but don't discard the only candidate because MusicBrainz
            # records a different or missing country.
            country = country.upper()
            narrowed = [c for c in exact if str(c.get("country") or "").upper() == country]
            if narrowed:
                exact = narrowed

        if not exact:
            if not candidates:
                raise ArtistNotFoundError(f"No MusicBrainz artist found for '{name}'")
            preview = ", ".join(
                f"{c.get('name')} ({c.get('disambiguation') or '?'})"
                for c in candidates[:3]
            )
            raise ArtistNotFoundError(
                f"No exact MusicBrainz match for '{name}'. Closest candidates: {preview}. "
                "Supply an MBID if this is the intended artist."
            )

        if len(exact) > 1:
            raise AmbiguousArtistError(name, exact)

        return _match_from_payload(exact[0], score=exact[0].get("score"))


def _match_from_payload(payload: dict[str, Any], score: int | None = None) -> ArtistMatch:
    mbid = payload.get("id")
    if not mbid:
        raise CollectionError("MusicBrainz artist payload is missing an MBID")
    return ArtistMatch(
        mbid=str(mbid),
        name=str(payload.get("name") or ""),
        country=payload.get("country"),
        disambiguation=payload.get("disambiguation"),
        artist_type=payload.get("type"),
        score=score,
    )
