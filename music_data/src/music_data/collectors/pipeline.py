"""Resumable, per-artist collection orchestration."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict
from typing import Any

from music_data.collectors.artist_collector import ArtistCollector, CollectionResult
from music_data.config import Config
from music_data.database import (
    create_job,
    finish_job,
    get_connection,
    get_db_stats,
    get_job_items,
    get_latest_job,
    log_fetch,
    record_seed_selection,
    update_job_item,
    upsert_job_item,
)
from music_data.fetchers.base import FetchEvent
from music_data.models import ArtistSeed

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: Config, *, transport: Any = None) -> None:
        self.config = config
        self.transport = transport

    def run(
        self,
        seed_artists: list[ArtistSeed] | None = None,
        *,
        db_path: str | None = None,
        resume: bool | None = None,
    ) -> dict[str, Any]:
        seeds = list(seed_artists if seed_artists is not None else self.config.collect.artists)
        db_path = db_path or self.config.database.path
        resume = self.config.collect.resume if resume is None else resume
        config_snapshot = {
            "database_path": db_path,
            "tracks_per_artist": self.config.collect.tracks_per_artist,
            "similar_limit": self.config.collect.similar_limit,
            "sources": {
                "musicbrainz": self.config.musicbrainz.enabled,
                "listenbrainz": self.config.listenbrainz.enabled,
                "lastfm": self.config.lastfm.enabled,
            },
        }

        with get_connection(db_path) as conn:
            existing = get_latest_job(conn) if resume else None
            if existing and existing["status"] in {"running", "partial", "failed"}:
                job_id = int(existing["id"])
                logger.info("Resuming collection job %s", job_id)
            else:
                job_id = create_job(conn, command="collect", config_snapshot=config_snapshot)
            for seed in seeds:
                record_seed_selection(
                    conn,
                    job_id=job_id,
                    name=seed.name,
                    mbid=seed.mbid,
                    reason=seed.reason or "User-configured seed",
                    country=seed.country,
                    genre=", ".join(seed.genres) or None,
                )

        fetch_events: list[FetchEvent] = []

        def on_fetch(event: FetchEvent) -> None:
            # The collector may hold a SQLite write transaction while making
            # requests. Persist provenance only after its per-artist commit.
            fetch_events.append(event)

        collector = ArtistCollector(self.config, transport=self.transport, fetch_log=on_fetch)
        results: list[CollectionResult] = []
        pending: list[tuple[ArtistSeed, int]] = []
        with get_connection(db_path) as conn:
            for seed in seeds:
                matching_items = conn.execute(
                    """SELECT id, identifier FROM ingestion_job_items
                       WHERE job_id = ? AND item_type = 'artist'
                         AND identifier IN (?, ?)
                       ORDER BY CASE WHEN identifier = ? THEN 0 ELSE 1 END
                       """,
                    (job_id, seed.identifier, seed.name, seed.identifier),
                ).fetchall()
                item = matching_items[0] if matching_items else None
                if item:
                    item_id = int(item["id"])
                    if item["identifier"] != seed.identifier:
                        conn.execute(
                            "UPDATE ingestion_job_items SET identifier = ? WHERE id = ?",
                            (seed.identifier, item_id),
                        )
                    for duplicate in matching_items[1:]:
                        conn.execute(
                            """UPDATE ingestion_job_items
                               SET status = 'skipped', error = NULL, updated_at = datetime('now')
                               WHERE id = ? AND status <> 'success'""",
                            (duplicate["id"],),
                        )
                else:
                    item_id = upsert_job_item(conn, job_id, "artist", seed.identifier)
                row = conn.execute("SELECT status FROM ingestion_job_items WHERE id = ?", (item_id,)).fetchone()
                if resume and row and row[0] == "success":
                    logger.info("Skipping completed artist %s", seed.name)
                    continue
                update_job_item(conn, item_id, status="in_progress", increment_attempt=True)
                pending.append((seed, item_id))

        completed = 0
        failed = 0
        try:
            for seed, item_id in pending:
                # Each artist owns its transaction, so interruption preserves prior work.
                with get_connection(db_path) as conn:
                    try:
                        result = collector.collect(seed, conn)
                    except Exception as exc:  # Keep unrelated seed processing moving.
                        result = CollectionResult(artist_name=seed.name, mbid=seed.mbid, error=str(exc))
                    results.append(result)
                    for event in fetch_events:
                        log_fetch(
                            conn,
                            provider=event.provider,
                            method=event.method,
                            url=event.url,
                            status_code=event.status_code,
                            from_cache=event.from_cache,
                            cache_path=event.cache_path,
                            response_hash=event.response_hash,
                        )
                    fetch_events.clear()
                    if result.success:
                        completed += 1
                        update_job_item(
                            conn,
                            item_id,
                            status="success",
                            error=("; ".join(result.provider_errors) or None),
                        )
                    else:
                        failed += 1
                        update_job_item(
                            conn,
                            item_id,
                            status="ambiguous" if result.ambiguous else "failed",
                            error=result.error,
                        )
                logger.info(
                    "%s: %s tracks, %s similarity edges",
                    seed.name,
                    result.recordings_count,
                    result.similar_count,
                )
        except KeyboardInterrupt:
            with get_connection(db_path) as conn:
                finish_job(conn, job_id, status="partial", error="Interrupted; run collect --resume")
            collector.close()
            raise
        finally:
            collector.close()

        with get_connection(db_path) as conn:
            items = get_job_items(conn, job_id)
            total_completed = sum(item["status"] == "success" for item in items)
            total_failed = sum(item["status"] in {"failed", "ambiguous"} for item in items)
            terminal = all(item["status"] in {"success", "skipped"} for item in items)
            status = "completed" if total_failed == 0 and terminal else (
                "partial" if total_completed else "failed"
            )
            stats = get_db_stats(conn)
            finish_job(
                conn,
                job_id,
                status=status,
                stats={"completed": total_completed, "failed": total_failed, "database": stats},
            )

        return {
            "job_id": job_id,
            "total_requested": len(seeds),
            "completed": completed,
            "failed": failed,
            "results": [result.to_dict() for result in results],
            "stats": stats,
        }
