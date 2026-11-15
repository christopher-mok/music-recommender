"""Build text input for artist embedding from database tables.

Text is composed of:
1. Artist name
2. Top tags (by count), deduplicated by name
3. Genre tags (tag_type='genre'), deduplicated
4. Artist bio summary (first ~500 chars), if available
5. Artist area/country

The template is configurable via ``spec()`` so that changing the template
produces a new spec hash and triggers full re-embedding.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)


def build_artist_text(
    conn: sqlite3.Connection,
    artist_id: int,
    name: str,
    sort_name: str | None = None,
    area_name: str | None = None,
    bio_summary: str | None = None,
) -> str:
    """Build the text input string for an artist.

    Template (configurable per spec):
        "{name} {sort_name} — {area} | {top_tags} | {genres} | {bio}"

    Returns the formatted string ready for embedding.
    """
    sort = (sort_name or "").strip()
    area = (area_name or "").strip()
    top_tags = _top_tags(conn, artist_id, limit=10)
    genres = _genre_tags(conn, artist_id)
    bio = _truncate((bio_summary or "").strip(), 500)

    parts: list[str] = [name]
    if sort and sort != name:
        parts.append(sort)
    if area:
        parts.append(area)
    if top_tags:
        parts.append("| " + " ".join(top_tags))
    if genres:
        parts.append("| " + " ".join(genres))
    if bio:
        parts.append("| " + bio)

    return " ".join(parts)


def _top_tags(conn: sqlite3.Connection, artist_id: int, limit: int = 10) -> list[str]:
    """Return the top N tags by count (deduplicated by tag name)."""
    rows = conn.execute(
        """
        SELECT t.name, MAX(at.count) AS cnt
        FROM artist_tags at
        JOIN tags t ON t.id = at.tag_id
        WHERE at.artist_id = ?
        GROUP BY lower(t.name)
        ORDER BY cnt DESC
        LIMIT ?
        """,
        (artist_id, limit),
    ).fetchall()
    return [row["name"] for row in rows]


def _genre_tags(conn: sqlite3.Connection, artist_id: int) -> list[str]:
    """Return genre-tag names for an artist (deduplicated)."""
    rows = conn.execute(
        """
        SELECT DISTINCT t.name
        FROM artist_tags at
        JOIN tags t ON t.id = at.tag_id
        WHERE at.artist_id = ? AND t.tag_type = 'genre'
        ORDER BY t.name
        """,
        (artist_id,),
    ).fetchall()
    return [row["name"] for row in rows]


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    # Truncate at word boundary
    truncated = text[:max_len]
    space = truncated.rfind(" ")
    if space > max_len * 0.5:
        truncated = truncated[:space]
    return truncated + "..."


def get_artists_for_embedding(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """Return artists eligible for text embedding.

    Each dict has: id, mbid, name, sort_name, area_name, bio_summary.
    """
    rows = conn.execute(
        """
        SELECT a.id, a.mbid, a.name, a.sort_name, a.area_name
        FROM artists a
        WHERE a.is_canonical = 1 AND a.mbid IS NOT NULL
        ORDER BY a.id
        """,
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        # Try to get bio from artist_bios if it exists
        bio_summary: str | None = None
        try:
            bio_row = conn.execute(
                "SELECT summary FROM artist_bios WHERE mbid = ?", (row["mbid"],)
            ).fetchone()
            bio_summary = bio_row["summary"] if bio_row else None
        except sqlite3.OperationalError:
            pass  # table doesn't exist
        result.append({
            "id": row["id"],
            "mbid": row["mbid"],
            "name": row["name"],
            "sort_name": row["sort_name"],
            "area_name": row["area_name"],
            "bio_summary": bio_summary,
        })
    return result
