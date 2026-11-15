"""Last.fm API client."""

from __future__ import annotations

import logging
from typing import Any

from music_data.fetchers.base import BaseFetcher, CollectionError

logger = logging.getLogger(__name__)

BASE_URL = "http://ws.audioscrobbler.com/2.0"


class LastfmFetcher(BaseFetcher):
    """Last.fm API client."""

    def __init__(self, api_key: str, max_rps: float = 2.0, **kwargs: Any) -> None:
        if not api_key:
            raise ValueError("Last.fm API key is required")
        super().__init__(
            base_url=BASE_URL,
            rate_limit=max_rps,
            **kwargs,
        )
        self.api_key = api_key
        self.client.headers["X-API-Key"] = api_key  # for reference; params use api_key

    def _add_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add API key to params."""
        params["api_key"] = self.api_key
        params["format"] = "json"
        return params

    def get_artist_info(self, mbid: str | None = None, artist: str | None = None) -> dict[str, Any]:
        """Fetch artist info including top tracks, tags, bio.

        Args:
            mbid: MusicBrainz ID (preferred)
            artist: Artist name (fallback if no mbid)

        Returns:
            Artist info dict with top tracks, tags, bio, etc.
        """
        if not mbid and not artist:
            raise CollectionError("Either mbid or artist name is required")

        params = self._add_params({
            "method": "artist.getInfo",
        })
        if mbid:
            params["mbid"] = mbid
        if artist:
            params["artist"] = artist

        data = self.request("", params)
        artist_data = data.get("artist", {})
        logger.info(
            f"Last.fm artist info: {artist_data.get('name')} "
            f"(playcount: {artist_data.get('stats', {}).get('listeners', 'N/A')})"
        )
        return artist_data

    def get_similar_artists(self, mbid: str | None = None, artist: str | None = None,
                            limit: int = 10) -> list[dict[str, Any]]:
        """Fetch similar artists.

        Args:
            mbid: MusicBrainz ID
            artist: Artist name (fallback if no mbid)
            limit: Maximum number of similar artists to return

        Returns:
            List of similar artist dicts with match scores.
        """
        if not mbid and not artist:
            raise CollectionError("Either mbid or artist name is required")

        params = self._add_params({
            "method": "artist.getSimilar",
            "limit": str(limit),
        })
        if mbid:
            params["mbid"] = mbid
        if artist:
            params["artist"] = artist

        data = self.request("", params)
        similar = data.get("similar", {}).get("artist", [])

        # Normalize response format
        normalized = []
        for sim in similar:
            normalized.append({
                "name": sim.get("name"),
                "mbid": sim.get("mbid"),
                "match": sim.get("match"),
                "id": sim.get("mbid") or sim.get("id"),
                "weight": float(sim.get("match", 0)) if sim.get("match") is not None else None,
            })

        logger.info(f"Found {len(normalized)} similar artists")
        return normalized

    def fetch(self, **kwargs: Any) -> dict[str, Any]:
        """Alias for get_artist_info to satisfy ABC."""
        return self.get_artist_info(**kwargs)
