"""Collect one artist, recordings, popularity, and similarity edges."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from music_data.config import Config
from music_data.database import (
    find_artist_by_name,
    get_artist_by_mbid,
    get_artist_recordings,
    get_or_create_tag,
    link_recording_release,
    save_artist_aliases,
    save_artist_popularity,
    save_artist_tags,
    save_recording_credits,
    save_recording_popularity,
    save_recording_tags,
    set_artist_status,
    upsert_artist,
    upsert_recording,
    upsert_release,
)
from music_data.fetchers.base import CollectionError, FetchEvent
from music_data.fetchers.listenbrainz import ListenBrainzFetcher
from music_data.fetchers.musicbrainz import AmbiguousArtistError, MusicBrainzFetcher
from music_data.models import ArtistSeed, SimilarArtist, Tag

logger = logging.getLogger(__name__)


@dataclass
class CollectionResult:
    artist_name: str
    mbid: str | None = None
    success: bool = False
    error: str | None = None
    ambiguous: bool = False
    metadata_fetched: bool = False
    recordings_count: int = 0
    similar_count: int = 0
    tags_count: int = 0
    provider_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artist_name": self.artist_name,
            "mbid": self.mbid,
            "success": self.success,
            "error": self.error,
            "ambiguous": self.ambiguous,
            "metadata_fetched": self.metadata_fetched,
            "recordings_count": self.recordings_count,
            "similar_count": self.similar_count,
            "tags_count": self.tags_count,
            "provider_errors": list(self.provider_errors),
        }


class ArtistCollector:
    """Artist-level transactional collector."""

    def __init__(self, config: Config, *, fetch_log: Any = None, transport: Any = None) -> None:
        self.config = config
        self.fetch_log = fetch_log
        self.transport = transport
        cache = None
        if config.cache.enabled:
            from music_data.cache import HttpCache

            cache = HttpCache(
                config.cache.directory,
                enabled=config.cache.enabled,
                read=config.cache.read,
                write=config.cache.write,
                ttl_seconds=config.cache.ttl_seconds,
            )
        common = {
            "timeout": config.http.timeout,
            "max_retries": config.http.max_retries,
            "retry_delay": config.http.retry_delay,
            "cache": cache,
            "on_fetch": fetch_log,
        }
        if transport is not None:
            common["transport"] = transport

        self.mb = MusicBrainzFetcher(
            user_agent=config.effective_user_agent(),
            rate_limit=config.musicbrainz.rate_limit,
            **common,
        ) if config.musicbrainz.enabled else None

        lb_common = dict(common)
        # ListenBrainz has two hosts, both sharing the same transport and cache.
        self.lb = ListenBrainzFetcher(
            api_url=config.listenbrainz.api_url,
            labs_url=config.listenbrainz.labs_url,
            algorithm=config.listenbrainz.algorithm,
            similar_limit=config.collect.similar_limit,
            rate_limit=config.listenbrainz.rate_limit,
            user_token=config.listenbrainz.user_token,
            **lb_common,
        ) if config.listenbrainz.enabled else None

    def close(self) -> None:
        if self.mb:
            self.mb.close()
        if self.lb:
            self.lb.close()

    def collect(self, seed: ArtistSeed, conn: sqlite3.Connection) -> CollectionResult:
        result = CollectionResult(artist_name=seed.name, mbid=seed.mbid)
        artist_id: int | None = None
        try:
            if not self.mb:
                raise CollectionError("MusicBrainz is disabled; canonical artist metadata unavailable")
            try:
                identity = self.mb.resolve_artist(seed.name, country=seed.country, mbid=seed.mbid)
            except AmbiguousArtistError as exc:
                identity = self._break_tie_by_listeners(exc)
            result.mbid = identity.mbid
            metadata = self.mb.lookup_artist(identity.mbid)
            result.metadata_fetched = True
            with conn:
                artist_id = self._save_artist(conn, metadata)
                self._save_selection_tags(conn, artist_id, seed.genres)
                result.tags_count = self._save_metadata_tags(conn, artist_id, metadata)
                save_artist_aliases(conn, artist_id, metadata.get("aliases", []))

                if self.lb:
                    self._collect_popularity_and_recordings(conn, artist_id, identity.mbid, result)
                    self._collect_similar(conn, artist_id, identity.mbid, result)

                set_artist_status(conn, artist_id, "complete", canonical=True)
            result.success = True
        except Exception as exc:
            result.error = str(exc)
            result.ambiguous = "Ambiguous artist" in str(exc)
            logger.exception("Collection failed for %s", seed.name)
            if artist_id is not None:
                with conn:
                    set_artist_status(conn, artist_id, "partial", canonical=True)
        return result

    # A same-name candidate wins only if it has at least this many ListenBrainz
    # listeners and this many times more than the runner-up.
    TIE_MIN_LISTENERS = 100
    TIE_RATIO = 10.0

    def _break_tie_by_listeners(self, exc: AmbiguousArtistError):
        """Resolve same-name artists by a clear listener-count winner, else re-raise.

        E.g. "JENNIE" (BLACKPINK) vs. a Finnish "Jennie": the famous one has
        orders of magnitude more listeners. Close calls still fail as ambiguous.
        """
        from music_data.fetchers.musicbrainz import _match_from_payload

        if not self.lb:
            raise exc
        ids = [c["id"] for c in exc.candidates if c.get("id")]
        pop = self.lb.artist_popularity(ids)
        ranked = sorted(ids, key=lambda m: pop.get(m, {}).get("listeners", 0), reverse=True)
        top = pop.get(ranked[0], {}).get("listeners", 0) if ranked else 0
        second = pop.get(ranked[1], {}).get("listeners", 0) if len(ranked) > 1 else 0
        if top < self.TIE_MIN_LISTENERS or top < self.TIE_RATIO * max(second, 1):
            raise exc
        winner = next(c for c in exc.candidates if c.get("id") == ranked[0])
        logger.info("Resolved ambiguous '%s' to %s (%s) by listeners %d vs %d",
                    exc.name, winner.get("name"), winner.get("disambiguation") or "-", top, second)
        return _match_from_payload(winner, score=winner.get("score"))

    @staticmethod
    def _save_artist(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
        area = data.get("area") or {}
        life_span = data.get("life-span") or data.get("life_span") or {}
        end_year = _year(life_span.get("end"))
        return upsert_artist(
            conn,
            mbid=str(data["id"]),
            name=str(data.get("name") or "Unknown"),
            sort_name=data.get("sort-name") or data.get("sort_name"),
            disambiguation=data.get("disambiguation"),
            artist_type=data.get("type"),
            country=data.get("country"),
            area_id=(area.get("id") if isinstance(area, dict) else None),
            area_name=(area.get("name") if isinstance(area, dict) else None),
            begin_year=_year(life_span.get("begin")),
            end_year=end_year,
            is_active=end_year is None,
            is_canonical=True,
            collection_status="partial",
            gender=(str(data["gender"]).lower() if data.get("gender") else None),
        )

    @staticmethod
    def _save_selection_tags(conn: sqlite3.Connection, artist_id: int, genres: tuple[str, ...]) -> None:
        if genres:
            save_artist_tags(
                conn,
                artist_id,
                [Tag(name=genre, tag_type="genre", source="seed") for genre in genres],
                default_source="seed",
            )

    @staticmethod
    def _save_metadata_tags(conn: sqlite3.Connection, artist_id: int, data: dict[str, Any]) -> int:
        tags: list[Tag] = []
        genre_names: set[str] = set()
        for entry in data.get("genres", []) or []:
            if entry.get("name"):
                genre_names.add(" ".join(str(entry["name"]).lower().split()))
                tags.append(Tag(entry["name"], count=_int(entry.get("count")), tag_type="genre"))
        for entry in data.get("tags", []) or []:
            # MusicBrainz repeats every genre in `tags`; keep only the genre row.
            if entry.get("name") and " ".join(str(entry["name"]).lower().split()) not in genre_names:
                tags.append(Tag(entry["name"], count=_int(entry.get("count")), tag_type="descriptive"))
        return save_artist_tags(conn, artist_id, tags, default_source="musicbrainz")

    def _collect_popularity_and_recordings(
        self, conn: sqlite3.Connection, artist_id: int, mbid: str, result: CollectionResult
    ) -> None:
        assert self.lb
        if not self.config.listenbrainz.user_token:
            # The top-recordings endpoint now returns HTTP 401 without a token.
            # Do not generate a failed request for every artist; use the
            # MusicBrainz browse fallback and label it honestly in the report.
            logger.info(
                "ListenBrainz token not configured; using MusicBrainz recording browse for %s",
                mbid,
            )
            self._collect_musicbrainz_recordings(conn, artist_id, mbid, result)
            return

        try:
            popular = self.lb.popular_recordings(mbid)
        except CollectionError as exc:
            result.provider_errors.append(f"ListenBrainz popular recordings: {exc}")
            logger.warning("ListenBrainz popular recordings failed for %s: %s", mbid, exc)
            self._collect_musicbrainz_recordings(conn, artist_id, mbid, result)
            return

        save_artist_popularity(
            conn,
            artist_id,
            provider="listenbrainz",
            measurements={"recordings_returned": len(popular)},
        )
        seen: set[str] = set()
        rank = 0
        for item in popular:
            recording_mbid = item.get("recording_mbid")
            title = str(item.get("recording_name") or "").strip()
            if not recording_mbid or not title or recording_mbid in seen:
                continue
            seen.add(recording_mbid)
            rank += 1
            self.save_popular_recording(conn, artist_id, item, rank, result.provider_errors)
            result.recordings_count += 1
            if result.recordings_count >= self.config.collect.tracks_per_artist:
                break

    def save_popular_recording(
        self,
        conn: sqlite3.Connection,
        artist_id: int,
        item: dict[str, Any],
        rank: int,
        errors: list[str] | None = None,
    ) -> int:
        """Save one ListenBrainz popular-recording item with its popularity rank."""
        recording_mbid = item.get("recording_mbid")
        title = str(item.get("recording_name") or "").strip()
        try:
            recording = self.mb.lookup_recording(recording_mbid) if self.mb else None
            recording_id = self._save_recording(conn, recording or item, artist_id)
        except CollectionError as exc:
            # Preserve the ListenBrainz identity and title even if MB lookup fails.
            if errors is not None:
                errors.append(f"Recording {recording_mbid}: {exc}")
            recording_id = upsert_recording(
                conn,
                mbid=str(recording_mbid),
                title=title,
                duration_ms=_int(item.get("length")),
                is_canonical=False,
                source="listenbrainz",
            )
            conn.execute(
                "INSERT OR IGNORE INTO recording_artists(recording_id, artist_id, credit_order, credit_name) VALUES (?, ?, 0, ?)",
                (recording_id, artist_id, item.get("artist_name")),
            )
        save_recording_popularity(
            conn,
            recording_id,
            provider="listenbrainz",
            measurements={
                "total_listen_count": _float(item.get("total_listen_count")),
                "total_user_count": _float(item.get("total_user_count")),
            },
            rank=rank,
        )
        return recording_id

    def _collect_musicbrainz_recordings(
        self,
        conn: sqlite3.Connection,
        artist_id: int,
        mbid: str,
        result: CollectionResult,
    ) -> None:
        """Best-effort recording coverage when a popularity source is unavailable."""
        if not self.mb:
            return
        candidates: list[dict[str, Any]] = []
        try:
            candidates = self.mb.search_recordings(mbid, limit=100)
        except CollectionError as search_exc:
            result.provider_errors.append(f"MusicBrainz recording search: {search_exc}")
            logger.warning("MusicBrainz recording search failed for %s: %s", mbid, search_exc)
        if not candidates:
            try:
                candidates = self.mb.browse_recordings(mbid, limit=100)
            except CollectionError as browse_exc:
                result.provider_errors.append(f"MusicBrainz recording browse: {browse_exc}")
                logger.warning("MusicBrainz recording browse failed for %s: %s", mbid, browse_exc)
                return

        selected: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for candidate in candidates:
            title = " ".join(str(candidate.get("title") or "").casefold().split())
            if not candidate.get("id") or not title or title in seen_titles:
                continue
            if not _is_usable_studio_title(title):
                continue
            seen_titles.add(title)
            selected.append(candidate)
            if len(selected) >= self.config.collect.tracks_per_artist:
                break

        for candidate in selected:
            # Browse omits releases; a direct lookup supplies release,
            # release-group, media, and canonical credit metadata.
            data = candidate
            try:
                data = self.mb.lookup_recording(str(candidate["id"]))
            except CollectionError as exc:
                result.provider_errors.append(f"Recording {candidate['id']}: {exc}")
                logger.warning("Recording lookup failed for %s: %s", candidate["id"], exc)
            self._save_recording(conn, data, artist_id)
            result.recordings_count += 1

    @staticmethod
    def _save_recording(conn: sqlite3.Connection, data: dict[str, Any], fallback_artist_id: int) -> int:
        artist_credit = data.get("artist-credit") or data.get("artist_credit") or []
        if not isinstance(artist_credit, list):
            artist_credit = []
        credits = []
        for order, credit in enumerate(artist_credit):
            artist = credit.get("artist") or {}
            credit_mbid = artist.get("id")
            credit_name = artist.get("name") or credit.get("name")
            if credit_mbid:
                row = conn.execute("SELECT id FROM artists WHERE mbid = ?", (credit_mbid,)).fetchone()
                if row:
                    credited_artist_id = int(row[0])
                else:
                    from music_data.database import upsert_stub_artist

                    credited_artist_id = upsert_stub_artist(conn, name=credit_name or "Unknown", mbid=credit_mbid)
            else:
                credited_artist_id = fallback_artist_id
            credits.append({
                "artist_id": credited_artist_id,
                "credit_order": order,
                "credit_name": credit_name,
                "join_phrase": credit.get("joinphrase"),
            })

        first_release = data.get("first-release-date") or data.get("first_release_date")
        recording_id = upsert_recording(
            conn,
            mbid=str(data["id"] if data.get("id") else data.get("recording_mbid")),
            title=str(data.get("title") or data.get("recording_name") or "Unknown"),
            duration_ms=_int(data.get("length")),
            disambiguation=data.get("disambiguation"),
            first_release_date=first_release,
            is_canonical=bool(data.get("id")),
            source="musicbrainz" if data.get("id") else "listenbrainz",
        )
        if not credits:
            credits = [{"artist_id": fallback_artist_id, "credit_order": 0}]
        save_recording_credits(conn, recording_id, credits)

        for medium in (data.get("releases") or [])[:5]:
            release_id = upsert_release(
                conn,
                mbid=medium.get("id"),
                title=str(medium.get("title") or "Unknown release"),
                release_group_mbid=medium.get("release-group", {}).get("id"),
                release_date=medium.get("date"),
                country=medium.get("country"),
                primary_type=medium.get("release-group", {}).get("primary-type"),
                secondary_type=", ".join(medium.get("release-group", {}).get("secondary-types", [])) or None,
            )
            link_recording_release(conn, recording_id, release_id)
        return recording_id

    def _collect_similar(
        self, conn: sqlite3.Connection, artist_id: int, mbid: str, result: CollectionResult
    ) -> None:
        assert self.lb
        try:
            similar = self.lb.similar_artists(mbid)
            from music_data.database import save_similarities

            result.similar_count = save_similarities(conn, artist_id, "listenbrainz", similar)
        except CollectionError as exc:
            result.provider_errors.append(f"ListenBrainz similarity: {exc}")
            logger.warning("ListenBrainz similarities failed for %s: %s", mbid, exc)


_REJECTED_TITLE_MARKERS = (
    "interview",
    "commentary",
    "rehearsal",
    "soundcheck",
    "a cappella",
    "acapella",
    "remix",
    " rmx",
    "live at",
    "live from",
    "(live)",
    "[live]",
    "demo",
    "instrumental",
    "karaoke",
    "alternate take",
    "early version",
    "session",
)


def _is_usable_studio_title(title: str) -> bool:
    """Reject obvious interviews, segues, and alternate versions.

    ListenBrainz popularity is the intended source; this filter only improves
    the fallback used when that source is unavailable. It is deliberately
    conservative about clearly non-studio material, not a genre judgment.
    """
    if not title or len(title) > 90:
        return False
    if title[0] in "[<{" or title in {"intro", "outro", "interlude", "untitled", "unknown"}:
        return False
    return not any(marker in title for marker in _REJECTED_TITLE_MARKERS)


def _year(value: Any) -> int | None:
    if not value:
        return None
    text = str(value)
    return int(text[:4]) if len(text) >= 4 and text[:4].isdigit() else None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
