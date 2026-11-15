"""Command line interface for the local collection pipeline."""

from __future__ import annotations

import json
from dataclasses import replace
import logging
import sys
from pathlib import Path
from typing import Any

import click

from music_data.collectors.pipeline import Pipeline
from music_data.config import Config, load_config
from music_data.database import get_connection, get_db_stats, init_db
from music_data.models import ArtistSeed
from music_data.reports.generator import generate_json_report, generate_report, validate_database

logger = logging.getLogger("music_data")


def _configure_output() -> None:
    """Avoid cp1252 console crashes when provider data contains non-ASCII text."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _configure_logging(verbose: bool) -> None:
    _configure_output()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


@click.group()
@click.option("-v", "--verbose", is_flag=True)
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Local music metadata and similarity collection."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


@cli.command("init")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
def init_command(config_path: str | None, db_path: str | None) -> None:
    """Initialize or migrate the SQLite database."""
    config = load_config(config_path)
    path = db_path or config.database.path
    init_db(path)
    click.echo(f"Database initialized: {path}")


POPULAR_RANGES = ["week", "month", "quarter", "half_yearly", "this_week", "this_month",
                  "this_year", "year", "all_time"]


def merge_seeds(base: list[ArtistSeed], extra: list[ArtistSeed]) -> list[ArtistSeed]:
    """Append extra seeds, skipping any already present by MBID or name.

    When a name-only base seed matches an extra seed that carries an MBID,
    the base seed adopts that MBID so it skips ambiguous name resolution.
    """
    mbids = {s.mbid for s in base if s.mbid}
    names = {s.name.casefold() for s in base}
    merged = list(base)
    for seed in extra:
        if seed.mbid and seed.mbid in mbids:
            continue
        if seed.name.casefold() in names:
            if seed.mbid:
                for i, existing in enumerate(merged):
                    if existing.name.casefold() == seed.name.casefold() and not existing.mbid:
                        merged[i] = replace(existing, mbid=seed.mbid)
                        mbids.add(seed.mbid)
                        break
            continue
        merged.append(seed)
        names.add(seed.name.casefold())
        if seed.mbid:
            mbids.add(seed.mbid)
    return merged


def _fetch_popular_seeds(config: Config, count: int, range_: str) -> list[ArtistSeed]:
    from music_data.fetchers.listenbrainz import ListenBrainzFetcher

    fetcher = ListenBrainzFetcher(
        api_url=config.listenbrainz.api_url,
        labs_url=config.listenbrainz.labs_url,
        algorithm=config.listenbrainz.algorithm,
        rate_limit=config.listenbrainz.rate_limit,
        user_token=config.listenbrainz.user_token,
        user_agent=config.effective_user_agent(),
        timeout=config.http.timeout,
        max_retries=config.http.max_retries,
        retry_delay=config.http.retry_delay,
    )
    try:
        return fetcher.popular_artist_seeds(count=count, range_=range_)
    finally:
        fetcher.close()


def _add_strategy_seeds(
    config: Config, quotas_path: str | None, expand_stubs: int | None, skip_collected: bool
) -> None:
    from music_data.collectors.artist_collector import ArtistCollector
    from music_data import seeding

    init_db(config.database.path)
    with get_connection(config.database.path) as conn:
        done_mbids = seeding.collected_mbids(conn)
        done_names = seeding.collected_names(conn)
        stubs = seeding.stub_expansion_seeds(conn, expand_stubs) if expand_stubs else []

    seeds = list(config.collect.artists)
    if quotas_path:
        collector = ArtistCollector(config)
        try:
            if not (collector.mb and collector.lb):
                raise click.UsageError("--quotas needs MusicBrainz and ListenBrainz enabled")
            exclude = done_mbids | {s.mbid for s in seeds if s.mbid}
            quota = seeding.quota_seeds(
                seeding.load_quota_slots(quotas_path), collector.mb, collector.lb,
                exclude_mbids=exclude,
            )
        finally:
            collector.close()
        seeds = merge_seeds(seeds, quota)
    seeds = merge_seeds(seeds, stubs)
    if skip_collected:
        seeds = [
            s for s in seeds
            if not ((s.mbid and s.mbid in done_mbids) or (not s.mbid and s.name.casefold() in done_names))
        ]
    config.collect.artists = seeds


@cli.command("collect")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--limit", type=click.IntRange(min=0), default=None)
@click.option("--tracks-per-artist", type=click.IntRange(min=1), default=None)
@click.option("--similar-limit", type=click.IntRange(min=1), default=None)
@click.option("--artist", "artist_names", multiple=True, help="Collect only these names (repeatable).")
@click.option("--mbid", "mbids", multiple=True, help="MBID corresponding to each --artist, in order.")
@click.option("--country", "countries", multiple=True, help="Country code corresponding to each --artist.")
@click.option("--reason", "reasons", multiple=True, help="Selection reason corresponding to each --artist.")
@click.option("--no-cache", is_flag=True, help="Do not read or write cached API responses.")
@click.option("--no-listenbrainz", is_flag=True, help="Disable ListenBrainz collection.")
@click.option("--resume", is_flag=True, help="Skip completed items in the latest incomplete job.")
@click.option("--refresh", is_flag=True, help="Disable cache reads and fetch current provider data.")
@click.option("--popular", "popular_count", type=click.IntRange(min=1), default=None,
              help="Also seed the top N ListenBrainz sitewide artists (needs one API call).")
@click.option("--popular-range", type=click.Choice(POPULAR_RANGES), default="this_year",
              show_default=True, help="Time range for --popular.")
@click.option("--popular-only", is_flag=True, help="Use only --popular seeds, not configured ones.")
@click.option("--quotas", "quotas_path", type=click.Path(exists=True, dir_okay=False),
              help="Add genre/region quota seeds from a TOML slot file.")
@click.option("--expand-stubs", type=click.IntRange(min=1), default=None,
              help="Add the N highest-scoring similarity stubs (gap-aware) as seeds.")
@click.option("--skip-collected", is_flag=True,
              help="Drop seeds already fully collected in the database.")
@click.option("--dry-run", is_flag=True, help="List selected artists without collecting them.")
def collect_command(
    config_path: str | None,
    db_path: str | None,
    limit: int | None,
    tracks_per_artist: int | None,
    similar_limit: int | None,
    artist_names: tuple[str, ...],
    mbids: tuple[str, ...],
    countries: tuple[str, ...],
    reasons: tuple[str, ...],
    no_cache: bool,
    no_listenbrainz: bool,
    resume: bool,
    refresh: bool,
    popular_count: int | None,
    popular_range: str,
    popular_only: bool,
    quotas_path: str | None,
    expand_stubs: int | None,
    skip_collected: bool,
    dry_run: bool,
) -> None:
    """Collect canonical artist metadata, representative tracks, and similarity."""
    config = load_config(config_path)
    config.apply_cli_overrides(
        db_path=db_path,
        cache=False if no_cache or refresh else None,
        tracks_per_artist=tracks_per_artist,
        similar_limit=similar_limit,
        resume=True if resume else None,
        refresh=True if refresh else None,
        listenbrainz=False if no_listenbrainz else None,
    )

    if artist_names:
        seeds = []
        for index, name in enumerate(artist_names):
            seeds.append(
                ArtistSeed(
                    name=name,
                    mbid=mbids[index] if index < len(mbids) else None,
                    country=countries[index] if index < len(countries) else None,
                    reason=reasons[index] if index < len(reasons) else "Specified on command line",
                )
            )
        config.collect.artists = seeds
    if popular_only and popular_count is None:
        raise click.UsageError("--popular-only requires --popular N")
    if popular_count is not None:
        popular = _fetch_popular_seeds(config, popular_count, popular_range)
        base = [] if popular_only else config.collect.artists
        config.collect.artists = merge_seeds(base, popular)
    if quotas_path or expand_stubs or skip_collected:
        _add_strategy_seeds(config, quotas_path, expand_stubs, skip_collected)
    if limit is not None:
        config.collect.artists = config.collect.artists[:limit]

    if dry_run:
        click.echo(f"Dry run — {len(config.collect.artists)} artist(s):")
        for index, seed in enumerate(config.collect.artists, 1):
            click.echo(f"{index}. {seed.name} | MBID={seed.mbid or 'resolve'} | {seed.reason}")
        return

    init_db(config.database.path)
    summary = Pipeline(config).run()
    # ensure_ascii=True keeps the default Windows cp1252 console from raising
    # UnicodeEncodeError on non-ASCII artist names or provider error strings.
    click.echo(json.dumps(summary, ensure_ascii=True, indent=2))


# ── 1b. collect popularity ─────────────────────────────────────────

@cli.command("collect-popularity")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
def collect_popularity_command(config_path: str | None, db_path: str | None) -> None:
    """Collect listenbrainz listener / listen counts for complete artists.

    This is additive-only: it inserts new measures (listens, listeners) into
    artist_popularity without touching the existing recordings_returned data.
    """
    from music_data.database import get_connection, init_db

    config = load_config(config_path)
    config.apply_cli_overrides(db_path=db_path)
    db_path = db_path or config.database.path
    init_db(db_path)

    from music_data.fetchers.listenbrainz import ListenBrainzFetcher
    from music_data.database import save_artist_popularity

    lb = ListenBrainzFetcher(
        algorithm=config.listenbrainz.algorithm,
        user_token=config.listenbrainz.user_token,
        rate_limit=config.listenbrainz.rate_limit,
    )

    with get_connection(db_path) as conn:
        # Get all complete-artist MBIDs (batch safe)
        mbids = [
            row["mbid"]
            for row in conn.execute(
                "SELECT mbid FROM artists WHERE collection_status = ? AND mbid IS NOT NULL",
                ("complete",),
            ).fetchall()
        ]

    total = len(mbids)
    click.echo(f"Collecting ListenBrainz popularity for {total} complete artists...")

    fetched = 0
    no_data = 0
    for start in range(0, total, 100):
        batch = mbids[start : start + 100]
        pop = lb.artist_popularity(batch)
        for mbid, counts in pop.items():
            with get_connection(db_path) as conn:
                artist_id = conn.execute(
                    "SELECT id FROM artists WHERE mbid = ?",
                    (mbid,),
                ).fetchone()
                if artist_id:
                    listens = counts.get("listens")
                    listeners = counts.get("listeners")
                    if listens is None and listeners is None:
                        no_data += 1
                        continue
                    save_artist_popularity(
                        conn,
                        artist_id["id"],
                        provider="listenbrainz",
                        measurements={
                            "listens": listens,
                            "listeners": listeners,
                        },
                    )
                    fetched += 1

    click.echo(f"Fetched & saved {fetched} artist(s)")
    if no_data:
        click.echo(f"  {no_data} artist(s) had no ListenBrainz data (will have null popularity)")
    click.echo("  New measures added: listens, listeners")
    click.echo("  Existing recordings_returned data is untouched.")


@cli.command("backfill-gender")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
def backfill_gender_command(config_path: str | None, db_path: str | None) -> None:
    """Fill artists.gender for collected artists (uses cached lookups when present)."""
    from music_data.collectors.artist_collector import ArtistCollector

    config = load_config(config_path)
    config.apply_cli_overrides(db_path=db_path)
    init_db(config.database.path)
    collector = ArtistCollector(config)
    updated = 0
    try:
        with get_connection(config.database.path) as conn:
            rows = conn.execute(
                "SELECT id, mbid FROM artists WHERE is_canonical = 1 AND mbid IS NOT NULL "
                "AND gender IS NULL AND COALESCE(artist_type, '') NOT IN ('Group', 'Orchestra', 'Choir')"
            ).fetchall()
            for row in rows:
                data = collector.mb.lookup_artist(row["mbid"])
                if data.get("gender"):
                    with conn:
                        conn.execute("UPDATE artists SET gender = ? WHERE id = ?",
                                     (str(data["gender"]).lower(), row["id"]))
                    updated += 1
    finally:
        collector.close()
    click.echo(f"Gender set for {updated} of {len(rows)} artists")


@cli.command("validate")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--json-output", type=click.Path(), help="Write machine-readable validation JSON.")
def validate_command(config_path: str | None, db_path: str | None, json_output: str | None) -> None:
    """Check database integrity and data coverage."""
    config = load_config(config_path)
    path = db_path or config.database.path
    with get_connection(path) as conn:
        result = validate_database(conn)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if json_output:
        Path(json_output).write_text(rendered, encoding="utf-8")
    # Keep the console form ASCII-safe even if the file itself is UTF-8.
    click.echo(json.dumps(result, ensure_ascii=True, indent=2))
    if not result["ok"]:
        raise click.ClickException("Database validation found integrity errors")


@cli.command("report")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--job-id", type=int)
@click.option("--format", "output_format", type=click.Choice(["markdown", "json"]), default="markdown")
@click.option("--output", type=click.Path())
def report_command(
    config_path: str | None,
    db_path: str | None,
    job_id: int | None,
    output_format: str,
    output: str | None,
) -> None:
    """Generate a coverage and validation report."""
    config = load_config(config_path)
    path = db_path or config.database.path
    with get_connection(path) as conn:
        content = (
            generate_json_report(conn, job_id=job_id)
            if output_format == "json"
            else generate_report(conn, job_id=job_id)
        )
    if output:
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"Report written to {output}")
    else:
        click.echo(content)


@cli.command("stats")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
def stats_command(config_path: str | None, db_path: str | None) -> None:
    config = load_config(config_path)
    path = db_path or config.database.path
    with get_connection(path) as conn:
        click.echo(json.dumps(get_db_stats(conn), indent=2))


@cli.group("embeddings")
@click.pass_context
def embeddings_group(ctx: click.Context) -> None:
    """Embedding pipeline: fetch previews, embed, aggregate, export."""
    pass


# ── 1. previews ───────────────────────────────────────────────────

@embeddings_group.command("previews")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--limit", type=click.IntRange(min=0), default=None,
              help="Only process this many artists.")
@click.option("--retry-misses", is_flag=True,
              help="Re-search tracks previously recorded in preview_misses.")
def previews_command(config_path: str | None, db_path: str | None, limit: int | None,
                     retry_misses: bool) -> None:
    """Fetch 30s preview clips from Deezer and cache locally.

    Resumable: tracks that already have a clip, or a recorded miss, are skipped.
    Each track is committed as soon as it is processed.
    """
    from collections import defaultdict
    from music_data.config import load_config
    from music_data.database import get_connection, init_db
    from music_data.fetchers.previews import DeezerPreviewFetcher

    config = load_config(config_path)
    db_path = db_path or config.database.path
    init_db(db_path)

    with get_connection(db_path) as conn:
        tracks = select_preview_tracks(conn, retry_misses=retry_misses)

    if not tracks:
        click.echo("No tracks need previews.")
        return

    by_artist: dict[int, list[dict]] = defaultdict(list)
    for t in tracks:
        by_artist[t["artist_id"]].append(t)
    artist_ids = list(by_artist)[:limit] if limit else list(by_artist)
    todo = [t for a in artist_ids for t in by_artist[a]]
    click.echo(f"{len(todo)} track(s) to search across {len(artist_ids)} artist(s)")

    fetcher = DeezerPreviewFetcher()
    counts = {"found": 0, "missed": 0, "error": 0}
    try:
        for n, track in enumerate(todo, 1):
            counts[fetch_one_preview(fetcher, db_path, track)] += 1
            if n % 50 == 0 or n == len(todo):
                click.echo(f"  {n}/{len(todo)}: {counts['found']} found, "
                           f"{counts['missed']} no match, {counts['error']} errors")
    finally:
        fetcher.close()

    click.echo(f"Previews: {counts['found']} stored, {counts['missed']} no match, "
               f"{counts['error']} errors "
               f"(see preview_misses; rerun with --retry-misses to try again)")


def fetch_one_preview(fetcher, db_path, track: dict) -> str:
    """Search, download and record one track's preview. Returns found/missed/error."""
    from music_data.database import get_connection

    try:
        clips = fetcher.find_clips([track])
        reason, detail = (None, None) if clips else ("no_match", None)
    except Exception as exc:  # network / decode: record and continue
        clips, reason, detail = [], "error", f"{type(exc).__name__}: {exc}"[:500]
    with get_connection(db_path) as conn:
        if clips and store_clip(conn, clips[0]) != track["track_id"]:
            # Same audio already stored for another recording (clips are
            # keyed by PCM hash). Mark it so the next run skips it.
            record_preview_miss(conn, track["track_id"], "duplicate_audio",
                                clips[0]["audio_sha256"])
            return "missed"
        if clips:
            conn.execute("DELETE FROM preview_misses WHERE track_id = ? "
                         "AND provider = 'deezer'", (track["track_id"],))
            return "found"
        record_preview_miss(conn, track["track_id"], reason, detail)
        return "error" if reason == "error" else "missed"


def artists_short_of_clips(conn, target: int) -> list[dict]:
    """Collected canonical artists with fewer than `target` distinct preview clips."""
    rows = conn.execute(
        """
        SELECT a.id, a.name, a.mbid,
               (SELECT COUNT(DISTINCT ac.track_id) FROM recording_artists ra
                  JOIN audio_clips ac ON ac.track_id = ra.recording_id
                 WHERE ra.artist_id = a.id) AS clips
        FROM artists a
        WHERE a.is_canonical = 1 AND a.collection_status = 'complete' AND a.mbid IS NOT NULL
        ORDER BY a.id
        """
    ).fetchall()
    return [{"artist_id": r["id"], "name": r["name"], "mbid": r["mbid"], "clips": r["clips"]}
            for r in rows if r["clips"] < target]


@embeddings_group.command("top-up")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--target", type=click.IntRange(min=1), default=5, show_default=True,
              help="Clips wanted per artist.")
@click.option("--max-extra", type=click.IntRange(min=1), default=10, show_default=True,
              help="Most replacement recordings to try per artist.")
@click.option("--limit", type=click.IntRange(min=0), default=None,
              help="Only process this many artists.")
def top_up_command(config_path: str | None, db_path: str | None, target: int,
                   max_extra: int, limit: int | None) -> None:
    """Replace tracks with no preview by the artist's next most popular recordings.

    For each collected artist with fewer than --target clips, walks down the
    artist's ListenBrainz popular-recordings list past the tracks already
    stored, saves each new recording (with its true popularity rank) and
    searches Deezer for it, until the artist reaches --target clips or
    --max-extra candidates were tried. Misses stay recorded in preview_misses.
    """
    from music_data.collectors.artist_collector import ArtistCollector
    from music_data.config import load_config
    from music_data.database import get_connection, init_db
    from music_data.fetchers.previews import DeezerPreviewFetcher

    config = load_config(config_path)
    db_path = db_path or config.database.path
    init_db(db_path)
    if not config.listenbrainz.user_token:
        raise click.UsageError("top-up needs a ListenBrainz token (popular recordings endpoint)")

    with get_connection(db_path) as conn:
        artists = artists_short_of_clips(conn, target)
    if limit:
        artists = artists[:limit]
    click.echo(f"{len(artists)} artist(s) below {target} clips")

    collector = ArtistCollector(config)
    fetcher = DeezerPreviewFetcher()
    added = tried = 0
    reached = 0
    try:
        for n, artist in enumerate(artists, 1):
            try:
                popular = collector.lb.popular_recordings(artist["mbid"])
            except Exception as exc:
                logger.warning("Popular recordings failed for %s: %s", artist["name"], exc)
                continue
            with get_connection(db_path) as conn:
                have = {r[0] for r in conn.execute(
                    "SELECT r.mbid FROM recordings r JOIN recording_artists ra "
                    "ON ra.recording_id = r.id WHERE ra.artist_id = ?",
                    (artist["artist_id"],))}
            clips, extra, seen = artist["clips"], 0, set()
            for rank, item in enumerate(
                    (i for i in popular if i.get("recording_mbid") and i.get("recording_name")), 1):
                mbid = item["recording_mbid"]
                if mbid in seen:
                    continue
                seen.add(mbid)
                if clips >= target or extra >= max_extra:
                    break
                if mbid in have:
                    continue
                extra += 1
                with get_connection(db_path) as conn:
                    with conn:
                        track_id = collector.save_popular_recording(
                            conn, artist["artist_id"], item, rank)
                    r = conn.execute("SELECT title, duration_ms FROM recordings WHERE id = ?",
                                     (track_id,)).fetchone()
                    has_clip = conn.execute("SELECT 1 FROM audio_clips WHERE track_id = ?",
                                            (track_id,)).fetchone()
                    has_miss = conn.execute("SELECT 1 FROM preview_misses WHERE track_id = ?",
                                            (track_id,)).fetchone()
                if has_clip or has_miss:
                    # Recording was already searched under a co-credited artist;
                    # it is now linked to this artist too, so its clip counts.
                    clips += 1 if has_clip else 0
                    continue
                outcome = fetch_one_preview(fetcher, db_path, {
                    "artist_id": artist["artist_id"], "artist": artist["name"],
                    "track": r["title"], "duration": r["duration_ms"],
                    "track_id": track_id, "mbid_track": mbid,
                })
                tried += 1
                if outcome == "found":
                    clips += 1
                    added += 1
            if clips >= target:
                reached += 1
            logger.info("%s: %d -> %d clips (%d replacement(s) tried)",
                        artist["name"], artist["clips"], clips, extra)
            if n % 25 == 0 or n == len(artists):
                click.echo(f"  {n}/{len(artists)} artists: {added} clips added, "
                           f"{tried} searched, {reached} reached {target}")
    finally:
        fetcher.close()
        collector.close()

    click.echo(f"Top-up: {added} clips added from {tried} replacement tracks; "
               f"{reached}/{len(artists)} artists now have {target}+ clips")


def select_preview_tracks(conn, *, retry_misses: bool = False) -> list[dict]:
    """Canonical tracks with popularity that still need a preview.

    A recording credited to several artists is returned once.
    """
    miss_filter = "" if retry_misses else (
        "AND NOT EXISTS (SELECT 1 FROM preview_misses pm WHERE pm.track_id = r.id)")
    rows = conn.execute(
        f"""
        SELECT MIN(a.id) AS artist_id, a.name AS artist_name,
               r.id AS track_id, r.mbid AS mbid_track,
               r.title AS track_name, r.duration_ms, MIN(rp.rank) AS rank
        FROM artists a
        JOIN recording_artists ra ON ra.artist_id = a.id
        JOIN recordings r ON r.id = ra.recording_id
        JOIN recording_popularity rp ON rp.recording_id = r.id
            AND rp.provider = 'listenbrainz' AND rp.measure = 'total_listen_count'
        WHERE a.is_canonical = 1 AND r.is_canonical = 1
          AND NOT EXISTS (SELECT 1 FROM audio_clips ac WHERE ac.track_id = r.id)
          {miss_filter}
        GROUP BY r.id
        ORDER BY MIN(a.id), MIN(rp.rank)
        """
    ).fetchall()
    return [{
        "artist_id": r["artist_id"], "artist": r["artist_name"], "track": r["track_name"],
        "duration": r["duration_ms"], "track_id": r["track_id"], "mbid_track": r["mbid_track"],
    } for r in rows]


def store_clip(conn, clip: dict) -> int:
    """Insert a clip; return the track_id that owns this audio hash."""
    conn.execute(
        """INSERT INTO audio_clips
               (audio_sha256, track_id, mbid_track, provider, provider_track_id,
                preview_url, duration_s, local_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(audio_sha256) DO UPDATE SET local_path = excluded.local_path""",
        (clip["audio_sha256"], clip["track_id"], clip["mbid_track"], clip["provider"],
         clip["provider_track_id"], clip["preview_url"], clip["duration_s"], clip["path"]))
    return conn.execute("SELECT track_id FROM audio_clips WHERE audio_sha256 = ?",
                        (clip["audio_sha256"],)).fetchone()[0]


def record_preview_miss(conn, track_id: int, reason: str, detail: str | None) -> None:
    conn.execute(
        """INSERT INTO preview_misses (track_id, provider, reason, detail)
           VALUES (?, 'deezer', ?, ?)
           ON CONFLICT(track_id, provider) DO UPDATE SET
             reason = excluded.reason, detail = excluded.detail,
             checked_at = datetime('now')""",
        (track_id, reason, detail))


# ── 2. embed ────────────────────────────────────────────────────────

@embeddings_group.command("embed")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--model", "model_name", default="fake",
              help="Model name (default: fake).")
@click.option("--modality", "modality", type=click.Choice(["audio", "text"]), default="text",
              help="Embedding modality.")
@click.option("--pooling", "pooling", type=click.Choice(["mean", "sum", "median"]),
              default="mean", help="Pooling method for artist vectors.")
def embed_command(
    config_path: str | None,
    db_path: str | None,
    model_name: str,
    modality: str,
    pooling: str,
) -> None:
    """Run embedding model on tracks (audio) or artist text (text)."""
    import time
    from music_data.config import load_config
    from music_data.database import get_connection, init_db
    from music_data.embeddings import embedder_for, list_models
    from music_data.embeddings.helpers import register_spec
    from music_data.embeddings.text_input import build_artist_text, get_artists_for_embedding

    config = load_config(config_path)
    db_path = db_path or config.database.path
    init_db(db_path)

    if model_name not in list_models():
        click.echo(f"Unknown model: {model_name}")
        click.echo(f"Available: {', '.join(list_models())}")
        return

    click.echo(f"Model: {model_name}, Modality: {modality}")
    model = embedder_for(model_name)

    if model.modality != modality:
        click.echo(f"Warning: model '{model_name}' modality={model.modality} != requested {modality}")

    with get_connection(db_path) as conn:
        spec_hash = register_spec(conn, embedder=model)
        spec = model.spec()
        click.echo(f"Spec hash: {spec_hash[:12]}… | dim={spec['dim']}")

        if modality == "text":
            _embed_text(conn, model, spec_hash)
        else:
            _embed_audio(conn, model, spec_hash, db_path)

        # Aggregate track vectors → artist vectors
        click.echo(f"Aggregating artist vectors (pooling={pooling})...")
        from music_data.embeddings.helpers import rebuild_all_artist_vectors
        count = rebuild_all_artist_vectors(conn, spec_hash, pooling)
        click.echo(f"Aggregated {count} artist(s)")


def _embed_text(conn, model, spec_hash: str) -> None:
    """Embed artist text inputs."""
    from music_data.embeddings.helpers import (
        get_existing_entity_ids,
        sha256_text,
        upsert_embedding,
    )
    from music_data.embeddings.text_input import build_artist_text, get_artists_for_embedding

    artists = get_artists_for_embedding(conn)
    # Filter out artists already embedded
    existing = get_existing_entity_ids(conn, spec_hash, "artist",
                                       [a["mbid"] for a in artists])
    to_embed = [a for a in artists if a["mbid"] not in existing]

    if not to_embed:
        click.echo("No artists to embed (already processed).")
        return

    click.echo(f"Embedding text for {len(to_embed)} artists...")
    # Map keys to match build_artist_text signature
    texts = [
        build_artist_text(
            conn,
            artist_id=a["id"],
            name=a["name"],
            sort_name=a["sort_name"],
            area_name=a["area_name"],
            bio_summary=a["bio_summary"],
        )
        for a in to_embed
    ]

    batch_size = 32
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        vectors = model.embed(batch)
        for j, txt in enumerate(batch):
            input_hash = sha256_text(txt)
            upsert_embedding(conn, spec_hash=spec_hash,
                           input_hash=input_hash, entity_type="artist",
                           entity_id=to_embed[i + j]["mbid"],
                           vectors=vectors[j:j+1])  # keep consistent shape
        click.echo(f"  batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}")

    click.echo(f"Embedded {len(to_embed)} artists.")


def _embed_audio(conn, model, spec_hash: str, db_path: str) -> None:
    """Embed audio clips (WAV/MP3) via the audio embedder."""
    import numpy as np
    from music_data.embeddings.helpers import upsert_embedding

    clips = conn.execute("""
        SELECT ac.audio_sha256, ac.track_id, ac.local_path
        FROM audio_clips ac
        WHERE ac.audio_sha256 NOT IN (
            SELECT input_hash FROM embeddings WHERE spec_hash = ?
        )
    """, (spec_hash,)).fetchall()

    if not clips:
        click.echo("No audio clips to embed.")
        return

    click.echo(f"Embedding {len(clips)} audio clip(s)...")
    stats = embed_clips(conn, model, spec_hash, clips,
                        progress=lambda msg: click.echo(msg))
    click.echo(f"Embedded {stats['ok']} clip(s), {stats['failed']} failed "
               f"(see embedding_failures).")


def embed_clips(conn, model, spec_hash: str, clips, *, batch_size: int = 8,
                progress=lambda msg: None) -> dict:
    """Embed clips in batches; isolate failures per clip and keep going.

    A failing batch is retried one clip at a time so one bad file (decode
    error, GPU OOM) only costs that clip. Failures go to embedding_failures
    and are retried automatically on the next run.
    """
    from pathlib import Path

    import numpy as np
    from music_data.embeddings.helpers import upsert_embedding

    stats = {"ok": 0, "failed": 0}

    def record_failure(clip, exc):
        conn.execute(
            """INSERT INTO embedding_failures (spec_hash, input_hash, error)
               VALUES (?, ?, ?)
               ON CONFLICT(spec_hash, input_hash) DO UPDATE SET
                 error = excluded.error, failed_at = datetime('now')""",
            (spec_hash, clip["audio_sha256"], f"{type(exc).__name__}: {exc}"[:500]))
        stats["failed"] += 1

    def store(clip, vec):
        upsert_embedding(conn, spec_hash=spec_hash, input_hash=clip["audio_sha256"],
                         entity_type="track", entity_id=str(clip["track_id"]),
                         vectors=np.asarray(vec, dtype=np.float32))
        conn.execute("DELETE FROM embedding_failures WHERE spec_hash = ? AND input_hash = ?",
                     (spec_hash, clip["audio_sha256"]))
        stats["ok"] += 1

    for i in range(0, len(clips), batch_size):
        batch = []
        for c in clips[i:i + batch_size]:
            if c["local_path"] and Path(c["local_path"]).exists():
                batch.append(c)
            else:
                record_failure(c, FileNotFoundError(c["local_path"]))
        if batch:
            try:
                vecs = model.embed([c["local_path"] for c in batch])
                for clip, vec in zip(batch, vecs):
                    store(clip, vec)
            except Exception:
                _free_gpu()
                for clip in batch:
                    try:
                        store(clip, model.embed([clip["local_path"]])[0])
                    except Exception as exc:
                        _free_gpu()
                        record_failure(clip, exc)
                        progress(f"  failed {clip['audio_sha256'][:12]}: {exc}")
        conn.commit()
        progress(f"  {min(i + batch_size, len(clips))}/{len(clips)} "
                 f"({stats['ok']} ok, {stats['failed']} failed)")
    return stats


def _free_gpu() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


# ── 3. aggregate ────────────────────────────────────────────────────

@embeddings_group.command("aggregate")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--spec-hash", type=str)
@click.option("--pooling", "pooling", type=click.Choice(["mean", "sum", "median"]),
              default="mean")
def aggregate_command(
    config_path: str | None,
    db_path: str | None,
    spec_hash: str | None,
    pooling: str,
) -> None:
    """Pool track embeddings into artist-level vectors."""
    from music_data.config import load_config
    from music_data.database import get_connection, init_db

    config = load_config(config_path)
    db_path = db_path or config.database.path
    init_db(db_path)

    with get_connection(db_path) as conn:
        # Find the latest spec hash if not specified
        if not spec_hash:
            row = conn.execute(
                "SELECT spec_hash FROM embedding_specs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                click.echo("No embedding specs found. Run 'embed' first.")
                return
            spec_hash = row["spec_hash"]

        from music_data.embeddings.helpers import rebuild_all_artist_vectors
        count = rebuild_all_artist_vectors(conn, spec_hash, pooling)
        click.echo(f"Aggregated {count} artist(s) with pooling={pooling}")


# ── 4. export ───────────────────────────────────────────────────────

@embeddings_group.command("export")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--db-path", type=click.Path())
@click.option("--spec-hash", type=str)
@click.option("--pooling", "pooling", type=click.Choice(["mean", "sum", "median"]),
              default="mean")
@click.option("--output-dir", "output_dir", type=click.Path(),
              default=".cache/music_data/embeddings/bundle")
@click.option("--level", type=click.Choice(["artist", "track"]), default="artist",
              help="artist: pooled artist vectors; track: one vector per audio clip.")
def export_command(
    config_path: str | None,
    db_path: str | None,
    spec_hash: str | None,
    pooling: str,
    output_dir: str,
    level: str,
) -> None:
    """Export artist or track embeddings as a float16 bundle + manifest."""
    from music_data.config import load_config
    from music_data.database import get_connection, init_db

    config = load_config(config_path)
    db_path = db_path or config.database.path
    init_db(db_path)

    with get_connection(db_path) as conn:
        # Find the latest spec hash if not specified
        if not spec_hash:
            row = conn.execute(
                "SELECT spec_hash FROM embedding_specs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                click.echo("No embedding specs found. Run 'embed' first.")
                return
            spec_hash = row["spec_hash"]

        from music_data.embeddings.export import export_artists, export_tracks
        from pathlib import Path

        if level == "track":
            export_tracks(conn, spec_hash, Path(output_dir))
        else:
            export_artists(conn, spec_hash, Path(output_dir), pooling)
        click.echo(f"Exported to {output_dir}")


# ------------------------------------------------------------------
# Publish: bundle the API data
# ------------------------------------------------------------------

@cli.command("publish")
@click.option(
    "--db-path",
    default="data/music_metadata_100.db",
    type=click.Path(exists=True),
    help="Path to the pipeline database.",
)
@click.option(
    "--output-dir",
    "output_dir",
    default="api_bundle",
    type=click.Path(),
    help="Directory for the API bundle.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate inputs but don't write files.",
)
def cmd_publish(db_path: str, output_dir: str, dry_run: bool) -> None:
    """Publish a read-only API bundle from the pipeline database.

    Creates a slim SQLite DB + float16 embedding arrays for use in
    the music recommendation API.
    """
    from music_data.database import connect

    click.echo(f"Reading pipeline DB: {db_path}")
    conn = connect(db_path)

    # Quick validation
    complete = conn.execute(
        "SELECT COUNT(*) AS cnt FROM artists WHERE collection_status = 'complete'"
    ).fetchone()["cnt"]
    audio_vecs = conn.execute(
        "SELECT COUNT(*) AS cnt FROM artist_vectors WHERE spec_hash = "
        "'cd8ee60e4b1799fa63e35d4f9301298ff02df2ca8d668a1753dd33052d32fe69'"
    ).fetchone()["cnt"]
    text_vecs = conn.execute(
        "SELECT COUNT(*) AS cnt FROM artist_vectors WHERE spec_hash = "
        "'4e64fdab4cf8c87f1fb178bc0bca6a99727bfbcb28a084d3678f38efc423b669'"
    ).fetchone()["cnt"]

    click.echo(f"Validated: {complete} complete artists, {audio_vecs} audio vectors, {text_vecs} text vectors")

    if dry_run:
        click.echo("Dry run — no files written.")
        conn.close()
        return

    from music_data.publish import publish as do_publish

    result = do_publish(db_path, output_dir)

    # Print summary
    click.echo()
    click.echo("=== Bundle published ===")
    click.echo(f"  Artists:   {result['artists']['complete']} complete + {result['artists']['stub']} stub")
    click.echo(f"  Tracks:    {result['tracks']:,}")
    click.echo(f"  Embeddings: audio={result['embeddings']['audio']}, text={result['embeddings']['text']}, tracks={result['embeddings']['tracks']}")
    click.echo(f"  Output:    {result['output_dir']}")
    click.echo(f"  Time:      {result['elapsed_seconds']}s")
    conn.close()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
