"""SQLite persistence layer for the music data pipeline.

All writes are designed to be idempotent: natural keys and UNIQUE constraints
allow a collection run to be repeated or resumed without creating duplicates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from music_data.models import SimilarArtist, Tag

logger = logging.getLogger(__name__)

PACKAGE_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


# ----------------------------------------------------------------------
# Connections and schema
# ----------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open and configure a SQLite connection.

    ``:memory:`` is passed through untouched; other paths are expanded and
    their parent directories are created.
    """
    path = str(db_path)
    if path == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        resolved = Path(path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(resolved))
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_connection(db_path: str | Path):
    """Context manager that commits on success and rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Migrations renamed to fix duplicate numbers. Databases that applied them under
# the old name must not re-run them (ALTER TABLE ADD COLUMN is not idempotent).
LEGACY_MIGRATION_NAMES = {
    "004_embeddings.sql": "002_embeddings.sql",
    "005_audio_clip_paths.sql": "003_audio_clip_paths.sql",
    "006_preview_misses.sql": "004_preview_misses.sql",
}


def init_db(db_path: str | Path) -> None:
    """Apply pending SQL migrations from the package migrations directory."""
    migration_files = sorted(PACKAGE_MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        raise FileNotFoundError(f"No migrations found in {PACKAGE_MIGRATIONS_DIR}")

    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                migration_file TEXT UNIQUE NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        applied = {
            row[0]
            for row in conn.execute("SELECT migration_file FROM schema_migrations").fetchall()
        }
        for migration_file in migration_files:
            if migration_file.name in applied:
                continue
            legacy = LEGACY_MIGRATION_NAMES.get(migration_file.name)
            if legacy in applied:
                conn.execute(
                    "UPDATE schema_migrations SET migration_file = ? WHERE migration_file = ?",
                    (migration_file.name, legacy),
                )
                continue
            logger.info("Applying migration %s", migration_file.name)
            conn.executescript(migration_file.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (migration_file) VALUES (?)",
                (migration_file.name,),
            )

        artist_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(artists)").fetchall()
        }
        if artist_columns and "id" not in artist_columns:
            raise RuntimeError(
                "This database uses the legacy schema. Back up the file and initialize a new "
                "database with the current pipeline; automatic legacy-schema migration is not "
                "available yet."
            )


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _year(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_tag_name(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


# ----------------------------------------------------------------------
# Fetch provenance
# ----------------------------------------------------------------------
def log_fetch(
    conn: sqlite3.Connection,
    *,
    provider: str,
    method: str,
    url: str,
    status_code: int | None,
    from_cache: bool,
    cache_path: str | None = None,
    response_hash: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log
            (provider, method, url, status_code, fetched_at, from_cache, cache_path, response_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            provider,
            method.upper(),
            url,
            status_code,
            _utc_now(),
            1 if from_cache else 0,
            cache_path,
            response_hash,
        ),
    )


# ----------------------------------------------------------------------
# Artists
# ----------------------------------------------------------------------
def upsert_artist(
    conn: sqlite3.Connection,
    *,
    mbid: str,
    name: str,
    sort_name: str | None = None,
    disambiguation: str | None = None,
    artist_type: str | None = None,
    country: str | None = None,
    area_id: str | None = None,
    area_name: str | None = None,
    begin_year: int | None = None,
    end_year: int | None = None,
    is_active: bool = True,
    is_canonical: bool = True,
    collection_status: str = "partial",
    gender: str | None = None,
) -> int:
    """Insert or update a canonical artist, returning its internal ID."""
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO artists (
            mbid, name, sort_name, disambiguation, artist_type, country,
            area_id, area_name, begin_year, end_year, is_active, is_canonical,
            collection_status, first_collected, last_collected, created_at, updated_at,
            gender
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mbid) DO UPDATE SET
            name = excluded.name,
            gender = excluded.gender,
            sort_name = excluded.sort_name,
            disambiguation = excluded.disambiguation,
            artist_type = excluded.artist_type,
            country = excluded.country,
            area_id = excluded.area_id,
            area_name = excluded.area_name,
            begin_year = excluded.begin_year,
            end_year = excluded.end_year,
            is_active = excluded.is_active,
            is_canonical = MAX(artists.is_canonical, excluded.is_canonical),
            collection_status = excluded.collection_status,
            last_collected = excluded.last_collected,
            updated_at = excluded.updated_at
        """,
        (
            mbid,
            name,
            sort_name,
            disambiguation,
            artist_type,
            country,
            area_id,
            area_name,
            begin_year,
            end_year,
            1 if is_active else 0,
            1 if is_canonical else 0,
            collection_status,
            now,
            now,
            now,
            now,
            gender,
        ),
    )
    row = conn.execute("SELECT id FROM artists WHERE mbid = ?", (mbid,)).fetchone()
    if row is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to upsert artist {mbid}")
    return int(row[0])


def upsert_stub_artist(
    conn: sqlite3.Connection,
    *,
    name: str,
    mbid: str | None = None,
    disambiguation: str | None = None,
    artist_type: str | None = None,
    country: str | None = None,
) -> int:
    """Create a minimal artist record for a provider-only / outside artist."""
    if mbid:
        row = conn.execute("SELECT id FROM artists WHERE mbid = ?", (mbid,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE artists
                SET name = COALESCE(NULLIF(name, ''), ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (name, _utc_now(), row[0]),
            )
            return int(row[0])

    row = conn.execute(
        """
        SELECT id FROM artists
        WHERE mbid IS NULL
          AND lower(name) = lower(?)
          AND COALESCE(disambiguation, '') = COALESCE(?, '')
        """,
        (name, disambiguation),
    ).fetchone()
    if row:
        return int(row[0])

    now = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO artists (
            mbid, name, disambiguation, artist_type, country, is_active,
            is_canonical, collection_status, first_collected, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 1, 0, 'stub', ?, ?, ?)
        """,
        (mbid, name, disambiguation, artist_type, country, now, now, now),
    )
    return int(cursor.lastrowid)


def get_artist_by_id(conn: sqlite3.Connection, artist_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM artists WHERE id = ?", (artist_id,)).fetchone()
    return dict(row) if row else None


def get_artist_by_mbid(conn: sqlite3.Connection, mbid: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM artists WHERE mbid = ?", (mbid,)).fetchone()
    return dict(row) if row else None


def find_artist_by_name(
    conn: sqlite3.Connection, name: str, country: str | None = None
) -> dict[str, Any] | None:
    query = "SELECT * FROM artists WHERE lower(name) = lower(?)"
    params: list[Any] = [name]
    if country:
        query += " AND upper(country) = upper(?)"
        params.append(country)
    query += " ORDER BY is_canonical DESC, id ASC LIMIT 1"
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def set_artist_status(
    conn: sqlite3.Connection,
    artist_id: int,
    status: str,
    *,
    canonical: bool | None = None,
) -> None:
    sets = ["collection_status = ?", "updated_at = ?"]
    params: list[Any] = [status, _utc_now()]
    if canonical is not None:
        sets.append("is_canonical = ?")
        params.append(1 if canonical else 0)
    if status == "complete":
        sets.append("last_collected = ?")
        params.append(_utc_now())
    params.append(artist_id)
    conn.execute(f"UPDATE artists SET {', '.join(sets)} WHERE id = ?", params)


def save_artist_aliases(
    conn: sqlite3.Connection,
    artist_id: int,
    aliases: Iterable[dict[str, Any]],
    *,
    source: str = "musicbrainz",
) -> int:
    """Replace an artist's aliases from one source."""
    conn.execute(
        "DELETE FROM artist_aliases WHERE artist_id = ? AND source = ?",
        (artist_id, source),
    )
    count = 0
    for alias in aliases:
        name = str(alias.get("name") or "").strip()
        if not name:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO artist_aliases
                (artist_id, name, sort_name, begin_year, end_year, alias_type, language, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artist_id,
                name,
                alias.get("sort_name"),
                _year(alias.get("begin")),
                _year(alias.get("end")),
                alias.get("type") or "",
                alias.get("language") or "",
                source,
            ),
        )
        count += 1
    return count


def get_or_create_tag(
    conn: sqlite3.Connection, name: str, tag_type: str = "descriptive"
) -> int:
    normalized = _normalize_tag_name(name)
    row = conn.execute(
        "SELECT id FROM tags WHERE normalized_name = ? AND tag_type = ?",
        (normalized, tag_type),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO tags (name, normalized_name, tag_type) VALUES (?, ?, ?)",
        (name.strip(), normalized, tag_type),
    )
    return int(cursor.lastrowid)


def _save_tags(
    conn: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    entity_id: int,
    tags: Iterable[Tag],
    default_source: str,
) -> int:
    if table not in {"artist_tags", "recording_tags"}:
        raise ValueError(f"Unsupported tag table: {table}")
    if id_column not in {"artist_id", "recording_id"}:
        raise ValueError(f"Unsupported id column: {id_column}")

    sources = {tag.source or default_source for tag in tags}
    for source in sources:
        conn.execute(
            f"DELETE FROM {table} WHERE {id_column} = ? AND source = ?",
            (entity_id, source),
        )

    count = 0
    for tag in tags:
        name = str(tag.name or "").strip()
        if not name:
            continue
        tag_id = get_or_create_tag(conn, name, tag.tag_type)
        conn.execute(
            f"""
            INSERT INTO {table} ({id_column}, tag_id, source, count, weight, retrieved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT({id_column}, tag_id, source) DO UPDATE SET
                count = excluded.count,
                weight = excluded.weight,
                retrieved_at = excluded.retrieved_at
            """,
            (
                entity_id,
                tag_id,
                tag.source or default_source,
                tag.count,
                tag.weight,
                _utc_now(),
            ),
        )
        count += 1
    return count


def save_artist_tags(
    conn: sqlite3.Connection,
    artist_id: int,
    tags: Iterable[Tag],
    *,
    default_source: str = "musicbrainz",
) -> int:
    return _save_tags(
        conn,
        table="artist_tags",
        id_column="artist_id",
        entity_id=artist_id,
        tags=tags,
        default_source=default_source,
    )


def save_recording_tags(
    conn: sqlite3.Connection,
    recording_id: int,
    tags: Iterable[Tag],
    *,
    default_source: str = "musicbrainz",
) -> int:
    return _save_tags(
        conn,
        table="recording_tags",
        id_column="recording_id",
        entity_id=recording_id,
        tags=tags,
        default_source=default_source,
    )


def get_artist_tags(
    conn: sqlite3.Connection, artist_id: int, *, source: str | None = None
) -> list[dict[str, Any]]:
    query = """
        SELECT t.name AS tag, t.tag_type, at.count, at.weight, at.source, at.retrieved_at
        FROM artist_tags at
        JOIN tags t ON t.id = at.tag_id
        WHERE at.artist_id = ?
    """
    params: list[Any] = [artist_id]
    if source:
        query += " AND at.source = ?"
        params.append(source)
    query += " ORDER BY t.tag_type, at.count DESC NULLS LAST"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def save_artist_external_id(
    conn: sqlite3.Connection,
    artist_id: int,
    *,
    provider: str,
    external_id: str,
    url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO artist_external_ids (artist_id, provider, external_id, url, retrieved_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(artist_id, provider, external_id) DO UPDATE SET
            url = COALESCE(excluded.url, artist_external_ids.url),
            retrieved_at = excluded.retrieved_at
        """,
        (artist_id, provider, str(external_id), url, _utc_now()),
    )


def save_recording_external_id(
    conn: sqlite3.Connection,
    recording_id: int,
    *,
    provider: str,
    external_id: str,
    url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO recording_external_ids (recording_id, provider, external_id, url, retrieved_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(recording_id, provider, external_id) DO UPDATE SET
            url = COALESCE(excluded.url, recording_external_ids.url),
            retrieved_at = excluded.retrieved_at
        """,
        (recording_id, provider, str(external_id), url, _utc_now()),
    )


# ----------------------------------------------------------------------
# Releases and recordings
# ----------------------------------------------------------------------
def upsert_release(
    conn: sqlite3.Connection,
    *,
    mbid: str | None,
    title: str,
    release_group_mbid: str | None = None,
    release_date: str | None = None,
    country: str | None = None,
    primary_type: str | None = None,
    secondary_type: str | None = None,
) -> int:
    now = _utc_now()
    if mbid:
        conn.execute(
            """
            INSERT INTO releases (
                mbid, title, release_group_mbid, release_date, country,
                primary_type, secondary_type, first_collected, last_collected
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mbid) DO UPDATE SET
                title = excluded.title,
                release_group_mbid = COALESCE(excluded.release_group_mbid,
                                              releases.release_group_mbid),
                release_date = COALESCE(excluded.release_date, releases.release_date),
                country = COALESCE(excluded.country, releases.country),
                primary_type = COALESCE(excluded.primary_type, releases.primary_type),
                secondary_type = COALESCE(excluded.secondary_type, releases.secondary_type),
                last_collected = excluded.last_collected
            """,
            (
                mbid,
                title,
                release_group_mbid,
                release_date,
                country,
                primary_type,
                secondary_type,
                now,
                now,
            ),
        )
        row = conn.execute("SELECT id FROM releases WHERE mbid = ?", (mbid,)).fetchone()
        return int(row[0])

    row = conn.execute(
        """
        SELECT id FROM releases
        WHERE mbid IS NULL AND lower(title) = lower(?)
          AND COALESCE(release_date, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (title, release_date),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        """
        INSERT INTO releases (
            mbid, title, release_group_mbid, release_date, country,
            primary_type, secondary_type, first_collected, last_collected
        )
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, release_group_mbid, release_date, country, primary_type, secondary_type, now, now),
    )
    return int(cursor.lastrowid)


def upsert_recording(
    conn: sqlite3.Connection,
    *,
    mbid: str,
    title: str,
    duration_ms: int | None = None,
    disambiguation: str | None = None,
    first_release_date: str | None = None,
    is_canonical: bool = True,
    source: str = "musicbrainz",
) -> int:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO recordings (
            mbid, title, duration_ms, disambiguation, first_release_date,
            is_canonical, source, first_collected, last_collected
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mbid) DO UPDATE SET
            title = excluded.title,
            duration_ms = COALESCE(excluded.duration_ms, recordings.duration_ms),
            disambiguation = COALESCE(excluded.disambiguation, recordings.disambiguation),
            first_release_date = COALESCE(excluded.first_release_date,
                                          recordings.first_release_date),
            is_canonical = MAX(recordings.is_canonical, excluded.is_canonical),
            last_collected = excluded.last_collected
        """,
        (
            mbid,
            title,
            duration_ms,
            disambiguation,
            first_release_date,
            1 if is_canonical else 0,
            source,
            now,
            now,
        ),
    )
    row = conn.execute("SELECT id FROM recordings WHERE mbid = ?", (mbid,)).fetchone()
    if row is None:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to upsert recording {mbid}")
    return int(row[0])


def save_recording_credits(
    conn: sqlite3.Connection, recording_id: int, credits: Sequence[dict[str, Any]]
) -> None:
    conn.execute("DELETE FROM recording_artists WHERE recording_id = ?", (recording_id,))
    for credit in credits:
        artist_id = credit.get("artist_id")
        if not artist_id:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO recording_artists
                (recording_id, artist_id, credit_order, credit_name, join_phrase)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                recording_id,
                int(artist_id),
                int(credit.get("credit_order", 0)),
                credit.get("credit_name"),
                credit.get("join_phrase"),
            ),
        )


def link_recording_release(
    conn: sqlite3.Connection,
    recording_id: int,
    release_id: int,
    *,
    disc_number: int | None = None,
    track_number: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO recording_releases (recording_id, release_id, disc_number, track_number)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(recording_id, release_id) DO UPDATE SET
            disc_number = COALESCE(excluded.disc_number, recording_releases.disc_number),
            track_number = COALESCE(excluded.track_number, recording_releases.track_number)
        """,
        (recording_id, release_id, disc_number, track_number),
    )


def get_artist_recordings(
    conn: sqlite3.Connection, artist_id: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, ra.credit_order, ra.credit_name
        FROM recordings r
        JOIN recording_artists ra ON ra.recording_id = r.id
        WHERE ra.artist_id = ?
        ORDER BY ra.credit_order, r.title
        """,
        (artist_id,),
    ).fetchall()
    return [dict(row) for row in rows]


# ----------------------------------------------------------------------
# Similarity and popularity
# ----------------------------------------------------------------------
def save_similarities(
    conn: sqlite3.Connection,
    source_artist_id: int,
    provider: str,
    items: Iterable[SimilarArtist],
) -> int:
    """Replace a source artist's similarities from one provider."""
    conn.execute(
        "DELETE FROM artist_similarities WHERE source_artist_id = ? AND provider = ?",
        (source_artist_id, provider),
    )
    count = 0
    for item in items:
        name = str(item.name or "").strip()
        if not name:
            continue
        target_id = upsert_stub_artist(
            conn,
            name=name,
            mbid=item.mbid,
            disambiguation=item.description,
            artist_type=item.artist_type,
            country=item.country,
        )
        if target_id == source_artist_id:
            continue
        conn.execute(
            """
            INSERT INTO artist_similarities (
                source_artist_id, target_artist_id, provider, score, raw_score,
                score_description, retrieved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_artist_id, target_artist_id, provider) DO UPDATE SET
                score = excluded.score,
                raw_score = excluded.raw_score,
                score_description = excluded.score_description,
                retrieved_at = excluded.retrieved_at
            """,
            (
                source_artist_id,
                target_id,
                provider,
                item.score,
                item.raw_score,
                item.description,
                _utc_now(),
            ),
        )
        count += 1
    return count


def get_similar_artists(
    conn: sqlite3.Connection, artist_id: int, *, provider: str | None = None
) -> list[dict[str, Any]]:
    query = """
        SELECT a.id, a.mbid, a.name, a.disambiguation, a.country,
               s.provider, s.score, s.raw_score, s.retrieved_at
        FROM artist_similarities s
        JOIN artists a ON a.id = s.target_artist_id
        WHERE s.source_artist_id = ?
    """
    params: list[Any] = [artist_id]
    if provider:
        query += " AND s.provider = ?"
        params.append(provider)
    query += " ORDER BY s.score DESC NULLS LAST, a.name"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def save_artist_popularity(
    conn: sqlite3.Connection,
    artist_id: int,
    *,
    provider: str,
    measurements: dict[str, float | int | None],
) -> None:
    for measure, value in measurements.items():
        if value is None:
            continue
        conn.execute(
            """
            INSERT INTO artist_popularity (artist_id, provider, measure, value, retrieved_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(artist_id, provider, measure) DO UPDATE SET
                value = excluded.value,
                retrieved_at = excluded.retrieved_at
            """,
            (artist_id, provider, measure, float(value), _utc_now()),
        )


def save_recording_popularity(
    conn: sqlite3.Connection,
    recording_id: int,
    *,
    provider: str,
    measurements: dict[str, float | int | None],
    rank: int | None = None,
) -> None:
    for measure, value in measurements.items():
        if value is None:
            continue
        conn.execute(
            """
            INSERT INTO recording_popularity
                (recording_id, provider, measure, value, rank, retrieved_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(recording_id, provider, measure) DO UPDATE SET
                value = excluded.value,
                rank = excluded.rank,
                retrieved_at = excluded.retrieved_at
            """,
            (recording_id, provider, measure, float(value), rank, _utc_now()),
        )


# ----------------------------------------------------------------------
# Collection jobs and seed provenance
# ----------------------------------------------------------------------
def create_job(
    conn: sqlite3.Connection,
    *,
    command: str,
    config_snapshot: dict[str, Any] | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO ingestion_jobs (command, status, config_json)
        VALUES (?, 'running', ?)
        """,
        (command, json.dumps(config_snapshot, ensure_ascii=False, default=str) if config_snapshot else None),
    )
    return int(cursor.lastrowid)


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    *,
    status: str,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_jobs
        SET status = ?,
            finished_at = CASE
                WHEN ? IN ('completed', 'partial', 'failed', 'cancelled') THEN ?
                ELSE finished_at
            END,
            stats_json = ?,
            error = ?
        WHERE id = ?
        """,
        (
            status,
            status,
            _utc_now(),
            json.dumps(stats, ensure_ascii=False, default=str) if stats is not None else None,
            error,
            job_id,
        ),
    )


def upsert_job_item(
    conn: sqlite3.Connection, job_id: int, item_type: str, identifier: str
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO ingestion_job_items (job_id, item_type, identifier)
        VALUES (?, ?, ?)
        """,
        (job_id, item_type, identifier),
    )
    row = conn.execute(
        """
        SELECT id FROM ingestion_job_items
        WHERE job_id = ? AND item_type = ? AND identifier = ?
        """,
        (job_id, item_type, identifier),
    ).fetchone()
    return int(row[0])


def update_job_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    status: str,
    error: str | None = None,
    increment_attempt: bool = False,
) -> None:
    conn.execute(
        """
        UPDATE ingestion_job_items
        SET status = ?,
            error = ?,
            attempt = attempt + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (status, error, 1 if increment_attempt else 0, _utc_now(), item_id),
    )


def get_job(conn: sqlite3.Connection, job_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_latest_job(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM ingestion_jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def get_job_items(conn: sqlite3.Connection, job_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM ingestion_job_items
        WHERE job_id = ?
        ORDER BY id
        """,
        (job_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def record_seed_selection(
    conn: sqlite3.Connection,
    *,
    job_id: int | None,
    name: str,
    mbid: str | None,
    reason: str,
    genre: str | None = None,
    country: str | None = None,
    selected: bool = True,
) -> None:
    conn.execute(
        """
        INSERT INTO seed_selections (job_id, name, mbid, reason, genre, country, selected)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, name) DO UPDATE SET
            mbid = COALESCE(excluded.mbid, seed_selections.mbid),
            reason = excluded.reason,
            genre = excluded.genre,
            country = excluded.country,
            selected = excluded.selected
        """,
        (job_id, name, mbid, reason, genre, country, 1 if selected else 0),
    )


# ----------------------------------------------------------------------
# Queries / statistics
# ----------------------------------------------------------------------
def get_db_stats(conn: sqlite3.Connection) -> dict[str, int]:
    tables = [
        "artists",
        "artist_aliases",
        "artist_tags",
        "tags",
        "releases",
        "recordings",
        "recording_artists",
        "recording_releases",
        "recording_tags",
        "artist_external_ids",
        "recording_external_ids",
        "artist_similarities",
        "artist_popularity",
        "recording_popularity",
        "ingestion_jobs",
        "ingestion_job_items",
        "seed_selections",
        "fetch_log",
    ]
    stats: dict[str, int] = {}
    for table in tables:
        try:
            stats[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            stats[table] = 0
    return stats


def get_database_size_bytes(db_path: str | Path) -> int:
    path = Path(str(db_path))
    if str(db_path) == ":memory:" or not path.exists():
        return 0
    total = path.stat().st_size
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            total += sidecar.stat().st_size
    return total
