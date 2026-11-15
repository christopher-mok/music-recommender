"""Database validation and Markdown/JSON coverage reports."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from music_data.database import get_db_stats


def validate_database(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return integrity and coverage checks without mutating the database."""
    fk_errors = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]
    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    stats = get_db_stats(conn)
    canonical_artists = conn.execute(
        "SELECT COUNT(*) FROM artists WHERE is_canonical = 1"
    ).fetchone()[0]
    artists_with_genres = conn.execute(
        """SELECT COUNT(DISTINCT at.artist_id) FROM artist_tags at
           JOIN tags t ON t.id = at.tag_id WHERE t.tag_type = 'genre'"""
    ).fetchone()[0]
    artists_with_recordings = conn.execute(
        """SELECT COUNT(DISTINCT ra.artist_id) FROM recording_artists ra
           JOIN artists a ON a.id = ra.artist_id WHERE a.is_canonical = 1"""
    ).fetchone()[0]
    recording_coverage = conn.execute(
        """SELECT COUNT(DISTINCT ar.artist_id) FROM recording_artists ar
           JOIN recordings r ON r.id = ar.recording_id
           JOIN artists a ON a.id = ar.artist_id
           WHERE r.mbid IS NOT NULL AND a.is_canonical = 1"""
    ).fetchone()[0]
    missing_canonical_mbid = conn.execute(
        "SELECT COUNT(*) FROM artists WHERE is_canonical = 1 AND mbid IS NULL"
    ).fetchone()[0]
    duplicate_aliases = conn.execute(
        """SELECT COUNT(*) FROM (
             SELECT artist_id, lower(name), alias_type, language, source
             FROM artist_aliases GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1
           )"""
    ).fetchone()[0]
    score_providers = [
        dict(row)
        for row in conn.execute(
            "SELECT provider, COUNT(*) AS relationships FROM artist_similarities GROUP BY provider ORDER BY provider"
        ).fetchall()
    ]
    recordings_by_source = [
        dict(row)
        for row in conn.execute(
            "SELECT source, COUNT(*) AS recordings FROM recordings GROUP BY source ORDER BY source"
        ).fetchall()
    ]
    artist_kinds = dict(
        conn.execute(
            "SELECT CASE WHEN is_canonical = 1 THEN 'canonical' ELSE 'stub' END, COUNT(*) "
            "FROM artists GROUP BY 1 ORDER BY 1"
        ).fetchall()
    )
    popularity_by_provider = [
        dict(row)
        for row in conn.execute(
            """SELECT 'artist' AS entity, provider, COUNT(*) AS measurements
               FROM artist_popularity GROUP BY provider
               UNION ALL
               SELECT 'recording' AS entity, provider, COUNT(*) AS measurements
               FROM recording_popularity GROUP BY provider
               ORDER BY entity, provider"""
        ).fetchall()
    ]
    api_failures = [
        dict(row)
        for row in conn.execute(
            """SELECT provider, method, url, status_code, fetched_at
               FROM fetch_log WHERE status_code >= 400
               ORDER BY id"""
        ).fetchall()
    ]
    genres = [
        dict(row)
        for row in conn.execute(
            """SELECT t.name, COUNT(DISTINCT at.artist_id) AS artist_count
               FROM artist_tags at JOIN tags t ON t.id = at.tag_id
               WHERE t.tag_type = 'genre'
               GROUP BY t.id ORDER BY artist_count DESC, t.name"""
        ).fetchall()
    ]
    tracks = [
        dict(row)
        for row in conn.execute(
            """SELECT a.name AS artist_name,
                      r.title,
                      r.first_release_date,
                      (SELECT rel.title
                       FROM recording_releases rr
                       JOIN releases rel ON rel.id = rr.release_id
                       WHERE rr.recording_id = r.id
                       ORDER BY rel.release_date
                       LIMIT 1) AS example_release
               FROM artists a
               JOIN recording_artists ra ON ra.artist_id = a.id
               JOIN recordings r ON r.id = ra.recording_id
               WHERE a.is_canonical = 1
               ORDER BY a.name, r.title"""
        ).fetchall()
    ]
    return {
        "ok": not fk_errors and all(item == "ok" for item in integrity),
        "integrity_check": integrity,
        "foreign_key_errors": fk_errors,
        "stats": stats,
        "coverage": {
            "canonical_artists": canonical_artists,
            "artists_with_genres": artists_with_genres,
            "artists_with_recordings": artists_with_recordings,
            "artists_with_mb_recording_ids": recording_coverage,
            "artists_missing_mbid": missing_canonical_mbid,
            "duplicate_alias_groups": duplicate_aliases,
            "genre_coverage": genres,
            "similarity_relationships_by_provider": score_providers,
            "artist_kinds": artist_kinds,
            "recordings_by_source": recordings_by_source,
            "popularity_by_provider": popularity_by_provider,
            "tracks": tracks,
            "failed_api_responses": len(api_failures),
            "api_failure_examples": api_failures[:10],
        },
    }


def _report_data(conn: sqlite3.Connection, job_id: int | None = None) -> dict[str, Any]:
    validation = validate_database(conn)
    jobs = []
    if job_id is not None:
        rows = conn.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ingestion_jobs ORDER BY id DESC LIMIT 10").fetchall()
    for raw in rows:
        job = dict(raw)
        try:
            job["config"] = json.loads(job.pop("config_json") or "{}")
        except ValueError:
            job["config"] = {}
        try:
            job["stats"] = json.loads(job.pop("stats_json") or "{}")
        except ValueError:
            job["stats"] = {}
        job_id_value = job["id"]
        job["items"] = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM ingestion_job_items WHERE job_id = ? ORDER BY id",
                (job_id_value,),
            ).fetchall()
        ]
        jobs.append(job)

    total_seconds = None
    latest = jobs[0] if jobs else None
    last_fetch = conn.execute("SELECT MAX(fetched_at) FROM fetch_log").fetchone()[0]
    if latest and latest.get("started_at") and last_fetch:
        try:
            start = datetime.fromisoformat(latest["started_at"].replace(" ", "T") + "+00:00")
            end = datetime.fromisoformat(str(last_fetch).replace(" ", "T") + "+00:00")
            total_seconds = max(0.0, (end - start).total_seconds())
        except ValueError:
            pass
    size_row = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute(
        "PRAGMA page_size"
    ).fetchone()[0]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "validation": validation,
        "jobs": jobs,
        "latest_job_duration_seconds": total_seconds,
        "database_size_bytes": size_row,
    }


def generate_json_report(conn: sqlite3.Connection, job_id: int | None = None) -> str:
    return json.dumps(_report_data(conn, job_id), ensure_ascii=False, indent=2)


def generate_report(conn: sqlite3.Connection, job_id: int | None = None) -> str:
    data = _report_data(conn, job_id)
    validation = data["validation"]
    coverage = validation["coverage"]
    lines = [
        "# Music data collection report",
        "",
        f"Generated: {data['generated_at']}",
        "",
        "## Validation",
        "",
        f"- Integrity: {'PASS' if validation['ok'] else 'FAIL'}",
        f"- Canonical artists: {coverage['canonical_artists']}",
        f"- Artists with genre tags: {coverage['artists_with_genres']}",
        f"- Artists with recordings: {coverage['artists_with_recordings']}",
        f"- Artists missing MBID: {coverage['artists_missing_mbid']}",
        f"- Duplicate alias groups: {coverage['duplicate_alias_groups']}",
        f"- Artists by kind: {coverage['artist_kinds']}",
        f"- Similarity relationships by provider: {coverage['similarity_relationships_by_provider']}",
        f"- Recordings by source: {coverage['recordings_by_source']}",
        f"- Popularity measurements by provider: {coverage['popularity_by_provider']}",
        f"- HTTP error responses (including retried attempts): {coverage['failed_api_responses']}",
        f"- Collection window (first run start to last API response): {data['latest_job_duration_seconds']} seconds (includes pauses between resume runs)",
        f"- SQLite pages size: {data['database_size_bytes']} bytes",
        "",
        "## Genre coverage",
        "",
        "| Genre | Artists |",
        "|---|---:|",
    ]
    lines.extend(f"| {item['name']} | {item['artist_count']} |" for item in coverage["genre_coverage"])
    if not coverage["genre_coverage"]:
        lines.append("| (none) | 0 |")

    lines.extend([
        "",
        "## Recorded tracks",
        "",
        "| Artist | Track | First release | Example release |",
        "|---|---|---|---|",
    ])
    for track in coverage["tracks"]:
        lines.append(
            f"| {track['artist_name']} | {track['title']} | "
            f"{track['first_release_date'] or ''} | {track['example_release'] or ''} |"
        )
    if not coverage["tracks"]:
        lines.append("| (none) | | | |")

    if not coverage["popularity_by_provider"]:
        lines.extend([
            "",
            "## Notes",
            "",
            "- No popularity measurements were collected. Set `LISTENBRAINZ_USER_TOKEN` "
            "to enable the ListenBrainz top-recordings endpoint; the current tracks use "
            "the relevance-ranked MusicBrainz fallback.",
        ])

    if coverage["api_failure_examples"]:
        lines.extend(["", "## HTTP error responses (retries may have succeeded)", ""])
        for failure in coverage["api_failure_examples"]:
            lines.append(
                f"- {failure['provider']} {failure['method']} HTTP {failure['status_code']}: {failure['url']}"
            )

    for job in data["jobs"]:
        lines.extend([
            "",
            f"## Job {job['id']} — {job['status']}",
            "",
            f"Started: {job['started_at']} | Finished: {job['finished_at'] or 'not finished'}",
            "",
            "| Artist key | Status | Attempts | Error |",
            "|---|---|---:|---|",
        ])
        for item in job["items"]:
            lines.append(
                f"| {item['identifier']} | {item['status']} | {item['attempt']} | {item['error'] or ''} |"
            )
        if not job["items"]:
            lines.append("| (none) | — | 0 | |")
    return "\n".join(lines)
