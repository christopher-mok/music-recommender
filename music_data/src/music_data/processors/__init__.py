"""Processors for normalizing API data into database rows."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MetadataProcessor:
    """Process MusicBrainz artist data into database operations."""

    @staticmethod
    def extract_artist_data(mb_data: dict[str, Any]) -> dict[str, Any]:
        """Extract and normalize the artist record."""
        return {
            "id": mb_data.get("id"),
            "name": mb_data.get("name"),
            "sort_name": mb_data.get("sort_name"),
            "disambiguation": mb_data.get("disambiguation"),
            "type": mb_data.get("type"),
            "country": mb_data.get("country"),
            "area": mb_data.get("area", {}),
            "life_span": mb_data.get("life_span", {}),
        }

    @staticmethod
    def extract_aliases(mb_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract artist aliases."""
        aliases = mb_data.get("aliases", [])
        return [
            {
                "name": a.get("name"),
                "sort_name": a.get("sort_name"),
                "begin": a.get("begin"),
                "end": a.get("end"),
                "type": a.get("type"),
                "language": a.get("language"),
            }
            for a in aliases if a.get("name")
        ]

    @staticmethod
    def extract_mb_tags(mb_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract MusicBrainz user tags."""
        tags = mb_data.get("tags", {}).get("tag", [])
        return [
            {
                "name": t.get("name"),
                "count": t.get("count"),
            }
            for t in tags if t.get("name")
        ]

    @staticmethod
    def extract_release_groups(mb_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract release groups for summary."""
        rgs = mb_data.get("release-groups", {}).get("release-group", [])
        return [
            {
                "title": rg.get("title"),
                "type": rg.get("type"),
                "year": rg.get("date", "")[:4] if rg.get("date") else None,
                "id": rg.get("id"),
            }
            for rg in rgs if rg.get("title")
        ]


class PopularityProcessor:
    """Process Last.fm artist info into database operations."""

    @staticmethod
    def extract_top_tracks(artist_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract top tracks from Last.fm artist info."""
        tracks = artist_data.get("tracks", {}).get("track", [])
        result = []
        for i, track in enumerate(tracks):
            duration = track.get("duration")
            if isinstance(duration, str):
                duration = int(duration)
            result.append({
                "name": track.get("name"),
                "mbid": track.get("mbid"),
                "id": track.get("mbid") or track.get("id"),
                "duration": duration,
                "playcount": track.get("streamable"),
                "rank": i + 1,
            })
        # Fix: extract playcount from stats
        stats = artist_data.get("stats", {})
        listeners = stats.get("listeners")
        playcount = stats.get("playcount")
        for i, track in enumerate(result):
            track["rank"] = i + 1
        return result

    @staticmethod
    def extract_lastfm_tags(artist_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tags from Last.fm artist info."""
        tags = artist_data.get("tags", {}).get("tag", [])
        return [
            {
                "name": t.get("name"),
                "count": int(t.get("count", 0)),
            }
            for t in tags if t.get("name")
        ]

    @staticmethod
    def extract_bio(artist_data: dict[str, Any]) -> dict[str, Any]:
        """Extract biography from Last.fm artist info."""
        bio = artist_data.get("bio", {})
        summary = bio.get("summary", "")
        content = bio.get("content", "")
        return {
            "summary": summary,
            "full_text": content,
            "language": bio.get("language", "en"),
        }

    @staticmethod
    def get_playcount(artist_data: dict[str, Any]) -> int | None:
        """Get overall playcount for an artist."""
        stats = artist_data.get("stats", {})
        return stats.get("playcount")


class SimilarityProcessor:
    """Process Last.fm similar artists into database operations."""

    @staticmethod
    def extract_similar_artists(similar_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize similar artist data."""
        result = []
        for sim in similar_data:
            match = sim.get("match")
            weight = None
            lastfm_match = None
            if match is not None:
                if isinstance(match, str):
                    try:
                        weight = float(match)
                    except ValueError:
                        weight = None
                else:
                    weight = float(match)
                lastfm_match = str(match)

            result.append({
                "name": sim.get("name"),
                "mbid": sim.get("mbid") or sim.get("id"),
                "weight": weight,
                "match": lastfm_match,
                "id": sim.get("mbid") or sim.get("id"),
            })
        return result
