"""Typed models used across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArtistSeed:
    """An artist requested for collection."""

    name: str
    mbid: str | None = None
    country: str | None = None
    reason: str = ""
    genres: tuple[str, ...] = ()

    @property
    def identifier(self) -> str:
        return self.mbid or self.name


@dataclass(frozen=True)
class ArtistMatch:
    """A resolved MusicBrainz artist identity."""

    mbid: str
    name: str
    country: str | None = None
    disambiguation: str | None = None
    artist_type: str | None = None
    score: int | None = None


@dataclass(frozen=True)
class Tag:
    name: str
    count: int | None = None
    weight: float | None = None
    tag_type: str = "descriptive"
    source: str = "musicbrainz"


@dataclass(frozen=True)
class SimilarArtist:
    """A provider-scoped similarity relationship."""

    mbid: str | None
    name: str
    score: float | None
    raw_score: str | None = None
    description: str | None = None
    artist_type: str | None = None
    country: str | None = None


@dataclass
class RecordingCandidate:
    """A recording selected for the catalog, before persistence."""

    mbid: str
    title: str
    duration_ms: int | None = None
    disambiguation: str | None = None
    first_release_date: str | None = None
    release_mbid: str | None = None
    release_title: str | None = None
    release_group_mbid: str | None = None
    release_date: str | None = None
    release_country: str | None = None
    release_primary_type: str | None = None
    release_secondary_type: str | None = None
    credits: list[dict] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    popularity: list[dict] = field(default_factory=list)
