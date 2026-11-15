"""Configuration management for the music data pipeline.

Configuration can come from a TOML file, environment variables, and CLI
overrides. CLI overrides take precedence over the file, which takes precedence
over environment defaults.

Environment variables:
    MUSIC_DATA_DB              Database path
    MUSIC_DATA_CACHE_DIR       Response cache directory
    MUSIC_DATA_USE_CACHE       0/1
    MUSICBRAINZ_USER_AGENT     MusicBrainz User-Agent (include contact info)
    LASTFM_API_KEY             Last.fm API key (only needed if Last.fm enabled)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - requires-python is >= 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from dotenv import load_dotenv

from music_data.models import ArtistSeed

DEFAULT_DB_PATH = "data/music_metadata_100.db"
DEFAULT_CACHE_DIR = ".cache/music_data"
DEFAULT_USER_AGENT = "music_data_pipeline/0.2 (https://example.invalid; music-data@example.invalid)"
DEFAULT_SIMILARITY_ALGORITHM = (
    "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30"
)

# Load local development credentials, if present. Existing environment variables
# take precedence over file values unless override=True is explicitly requested.
load_dotenv()

DEFAULT_SEED_ARTISTS: list[ArtistSeed] = [
    ArtistSeed("Radiohead", mbid="a74b1b7f-71a5-4011-9441-d0b5e4122711", country="GB",
               reason="Alternative rock / electronic crossover; well documented."),
    ArtistSeed("Björk", country="IS",
               reason="Art pop / electronic; strong critical and listener footprint."),
    ArtistSeed("Miles Davis", country="US",
               reason="Jazz canon; broad historical similarity network."),
    ArtistSeed("Aphex Twin", mbid="f22942a1-6f70-4f48-866e-238cb2308fbd", country="GB",
               reason="Electronic / ambient; connects several electronic subgenres."),
    ArtistSeed("Fela Kuti", country="NG",
               reason="Afrobeat; African representation and regional diversity."),
    ArtistSeed("Missy Elliott", country="US",
               reason="Hip-hop / R&B; 1990s-2000s US pop crossover."),
    ArtistSeed("M.I.A.", mbid="7cf0ea9d-86b9-4dad-ba9e-2355a64899ea", country="GB",
               reason="Electropop / world; multicultural and diaspora perspective."),
    ArtistSeed("Ólafur Arnalds", country="IS",
               reason="Neo-classical / ambient; non-rock instrumental coverage."),
    ArtistSeed("Tame Impala", country="AU",
               reason="Psychedelic rock / pop; Australian representation."),
    ArtistSeed("Yoko Ono", mbid="b0b33754-a664-43b7-ba00-be0dc4ec2396", country="JP",
               reason="Avant-garde / experimental; non-Western and experimental coverage."),
]


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class DatabaseConfig:
    path: str = DEFAULT_DB_PATH

    def __post_init__(self) -> None:
        env_path = os.environ.get("MUSIC_DATA_DB") or os.environ.get("DB_PATH")
        if env_path:
            self.path = env_path


@dataclass
class HttpConfig:
    timeout: float = 30.0
    max_retries: int = 5
    retry_delay: float = 1.0
    user_agent: str = DEFAULT_USER_AGENT

    def __post_init__(self) -> None:
        env_ua = os.environ.get("MUSICBRAINZ_USER_AGENT")
        if env_ua:
            self.user_agent = env_ua


@dataclass
class CacheConfig:
    enabled: bool = True
    read: bool = True
    write: bool = True
    directory: str = DEFAULT_CACHE_DIR
    ttl_seconds: int = 0

    def __post_init__(self) -> None:
        env_dir = os.environ.get("MUSIC_DATA_CACHE_DIR")
        if env_dir:
            self.directory = env_dir
        if "MUSIC_DATA_USE_CACHE" in os.environ:
            self.enabled = _as_bool(os.environ["MUSIC_DATA_USE_CACHE"], True)


@dataclass
class MusicBrainzConfig:
    enabled: bool = True
    rate_limit: float = 1.0
    user_agent: str = ""

    def __post_init__(self) -> None:
        # A local .env / environment value must win over a config-file example
        # placeholder, otherwise live requests can silently use a fake contact.
        env_ua = os.environ.get("MUSICBRAINZ_USER_AGENT")
        if env_ua:
            self.user_agent = env_ua


@dataclass
class ListenBrainzConfig:
    enabled: bool = True
    rate_limit: float = 2.0
    similar_limit: int = 50
    algorithm: str = DEFAULT_SIMILARITY_ALGORITHM
    user_token: str = ""
    api_url: str = "https://api.listenbrainz.org"
    labs_url: str = "https://labs.api.listenbrainz.org"


@dataclass
class LastfmConfig:
    enabled: bool = False
    api_key: str = ""
    rate_limit: float = 5.0
    similar_limit: int = 10
    api_url: str = "https://ws.audioscrobbler.com/2.0"

    def __post_init__(self) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("LASTFM_API_KEY", "")


@dataclass
class CollectConfig:
    tracks_per_artist: int = 5
    similar_limit: int = 50
    resume: bool = False
    refresh: bool = False
    artists: list[ArtistSeed] = field(default_factory=list)


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    musicbrainz: MusicBrainzConfig = field(default_factory=MusicBrainzConfig)
    listenbrainz: ListenBrainzConfig = field(default_factory=ListenBrainzConfig)
    lastfm: LastfmConfig = field(default_factory=LastfmConfig)
    collect: CollectConfig = field(default_factory=CollectConfig)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Build a config from a parsed TOML dictionary without mutating it."""
        database_data = dict(data.get("database", {}))
        http_data = dict(data.get("http", {}))
        cache_data = dict(data.get("cache", {}))
        mb_data = dict(data.get("musicbrainz", {}))
        lb_data = dict(data.get("listenbrainz", {}))
        lf_data = dict(data.get("lastfm", {}))
        collect_data = dict(data.get("collect", {}))

        # Support both [[collect.artists]] and legacy [[collect.seed_artists]].
        raw_artists = collect_data.get("artists", collect_data.get("seed_artists", []))
        seeds = [_seed_from_dict(item) for item in raw_artists] if raw_artists else list(
            DEFAULT_SEED_ARTISTS
        )

        return cls(
            database=DatabaseConfig(
                path=str(database_data.get("path", DEFAULT_DB_PATH)),
            ),
            http=HttpConfig(
                timeout=_as_float(http_data.get("timeout"), 30.0),
                max_retries=_as_int(http_data.get("max_retries"), 5),
                retry_delay=_as_float(http_data.get("retry_delay"), 1.0),
                user_agent=str(http_data.get("user_agent", DEFAULT_USER_AGENT)),
            ),
            cache=CacheConfig(
                enabled=_as_bool(cache_data.get("enabled"), True),
                read=_as_bool(cache_data.get("read"), True),
                write=_as_bool(cache_data.get("write"), True),
                directory=str(cache_data.get("directory", DEFAULT_CACHE_DIR)),
                ttl_seconds=_as_int(cache_data.get("ttl_seconds"), 0),
            ),
            musicbrainz=MusicBrainzConfig(
                enabled=_as_bool(mb_data.get("enabled"), True),
                rate_limit=_as_float(mb_data.get("rate_limit"), 1.0),
                user_agent=str(mb_data.get("user_agent", "")),
            ),
            listenbrainz=ListenBrainzConfig(
                enabled=_as_bool(lb_data.get("enabled"), True),
                rate_limit=_as_float(lb_data.get("rate_limit"), 2.0),
                similar_limit=_as_int(lb_data.get("similar_limit"), 50),
                algorithm=str(lb_data.get("algorithm", DEFAULT_SIMILARITY_ALGORITHM)),
                user_token=str(lb_data.get("user_token") or os.environ.get("LISTENBRAINZ_USER_TOKEN", "")),
                api_url=str(lb_data.get("api_url", "https://api.listenbrainz.org")),
                labs_url=str(lb_data.get("labs_url", "https://labs.api.listenbrainz.org")),
            ),
            lastfm=LastfmConfig(
                enabled=_as_bool(lf_data.get("enabled"), False),
                api_key=str(lf_data.get("api_key", "")),
                rate_limit=_as_float(lf_data.get("rate_limit"), 5.0),
                similar_limit=_as_int(lf_data.get("similar_limit"), 10),
                api_url=str(lf_data.get("api_url", "https://ws.audioscrobbler.com/2.0")),
            ),
            collect=CollectConfig(
                tracks_per_artist=max(1, _as_int(collect_data.get("tracks_per_artist"), 5)),
                similar_limit=max(1, _as_int(collect_data.get("similar_limit"), 50)),
                resume=_as_bool(collect_data.get("resume"), False),
                refresh=_as_bool(collect_data.get("refresh"), False),
                artists=seeds,
            ),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        return cls.from_dict(data)

    @classmethod
    def from_env_only(cls) -> Config:
        return cls.from_dict({})

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def effective_user_agent(self) -> str:
        return self.musicbrainz.user_agent or self.http.user_agent

    def with_seeds(self, seeds: list[ArtistSeed]) -> Config:
        self.collect.artists = list(seeds)
        return self

    def apply_cli_overrides(
        self,
        *,
        db_path: str | None = None,
        cache: bool | None = None,
        tracks_per_artist: int | None = None,
        similar_limit: int | None = None,
        resume: bool | None = None,
        refresh: bool | None = None,
        listenbrainz: bool | None = None,
        lastfm: bool | None = None,
    ) -> Config:
        if db_path:
            self.database.path = db_path
        if cache is not None:
            self.cache.enabled = cache
            self.cache.read = cache
            self.cache.write = cache
        if tracks_per_artist is not None:
            self.collect.tracks_per_artist = max(1, tracks_per_artist)
        if similar_limit is not None:
            self.collect.similar_limit = max(1, similar_limit)
        if resume is not None:
            self.collect.resume = resume
        if refresh is not None:
            self.collect.refresh = refresh
        if listenbrainz is not None:
            self.listenbrainz.enabled = listenbrainz
        if lastfm is not None:
            self.lastfm.enabled = lastfm
        return self


def _seed_from_dict(item: dict[str, Any]) -> ArtistSeed:
    genres = item.get("genres", [])
    if isinstance(genres, str):
        genres = [genres]
    return ArtistSeed(
        name=str(item.get("name", "")).strip(),
        mbid=(str(item["mbid"]).strip() if item.get("mbid") else None),
        country=(str(item["country"]).strip().upper() if item.get("country") else None),
        reason=str(item.get("reason", "")),
        genres=tuple(str(g) for g in genres),
    )


def load_config(config_path: str | None = None) -> Config:
    """Load config from an explicit path or the conventional seed file."""
    if config_path:
        return Config.from_file(config_path)
    conventional = Path("data/seed_artists.toml")
    if conventional.exists():
        return Config.from_file(conventional)
    return Config.from_env_only()
