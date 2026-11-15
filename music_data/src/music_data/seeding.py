"""Seed selection strategies beyond the hand-written seed list.

* Quota seeding: one MusicBrainz Lucene query per slot (genre, region, era),
  ranked by ListenBrainz listener counts, top N per slot. This spreads the
  catalog across genres and regions instead of following one popularity list.
* Gap-aware stub expansion: picks similarity stubs to fully collect, scored
  by how many collected artists link to them, weighted toward links from
  artists in under-represented genres.
"""

from __future__ import annotations

import logging
import sqlite3
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from music_data.models import ArtistSeed

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuotaSlot:
    name: str
    query: str
    count: int
    group: str = "genre"


def load_quota_slots(path: str | Path) -> list[QuotaSlot]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    slots = []
    for item in data.get("slots", []):
        slots.append(QuotaSlot(
            name=str(item["name"]),
            query=str(item["query"]),
            count=int(item.get("count", data.get("default_count", 15))),
            group=str(item.get("group", "genre")),
        ))
    return slots


def quota_seeds(
    slots: list[QuotaSlot],
    mb: Any,
    lb: Any,
    *,
    exclude_mbids: set[str] = frozenset(),
    min_listeners: int = 1,
) -> list[ArtistSeed]:
    """Top-N artists per slot by ListenBrainz listeners.

    An artist chosen by an earlier slot is not reused, so each slot adds new
    artists. Candidates with no ListenBrainz listeners are dropped: they are
    usually obscure duplicates and would have no popularity or similarity data.
    """
    taken: set[str] = set(exclude_mbids)
    seeds: list[ArtistSeed] = []
    for slot in slots:
        candidates = [c for c in mb.search_artists_query(slot.query, limit=100) if c.get("id")]
        if not candidates:
            logger.warning("Quota slot %s matched no artists", slot.name)
            continue
        popularity = lb.artist_popularity([c["id"] for c in candidates])
        ranked = sorted(
            (c for c in candidates if popularity.get(c["id"], {}).get("listeners", 0) >= min_listeners),
            key=lambda c: popularity[c["id"]]["listeners"],
            reverse=True,
        )
        added = 0
        for c in ranked:
            if c["id"] in taken:
                continue
            taken.add(c["id"])
            added += 1
            seeds.append(ArtistSeed(
                name=str(c.get("name") or c["id"]),
                mbid=c["id"],
                country=c.get("country"),
                reason=(f"Quota slot '{slot.name}' ({slot.group}): #{added} by ListenBrainz "
                        f"listeners ({popularity[c['id']]['listeners']})"),
            ))
            if added >= slot.count:
                break
        logger.info("Quota slot %s: %d/%d artists (%d candidates)",
                    slot.name, added, slot.count, len(candidates))
    return seeds


def collected_mbids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT mbid FROM artists WHERE is_canonical = 1 AND collection_status = 'complete' "
        "AND mbid IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows}


def collected_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM artists WHERE is_canonical = 1 AND collection_status = 'complete'"
    ).fetchall()
    return {str(r[0]).casefold() for r in rows}


# Broad genre families for gap scoring. Order matters: first match wins, so
# specific families (metal, k/j-pop, latin) come before generic ones (rock, pop).
GENRE_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    ("metal", ("metal", "core")),
    ("k/j-pop", ("k-pop", "j-pop", "j-rock", "korean", "anime", "city pop")),
    ("latin", ("latin", "reggaeton", "salsa", "bossa", "mpb", "tango", "bachata", "cumbia",
               "sertanejo", "corrido", "banda", "norteño", "funk brasileiro")),
    ("african/caribbean", ("afro", "highlife", "amapiano", "reggae", "dancehall", "ska",
                           "soca", "jùjú", "mbalax", "morna", "marabi")),
    ("world/traditional", ("world", "arabic", "qawwali", "hindustani", "filmi", "bollywood",
                           "ghazal", "carnatic", "flamenco", "ethio", "tarab")),
    ("synthwave/retro", ("synthwave", "retrowave", "outrun", "darksynth", "chillwave", "vaporwave")),
    ("edm/dance", ("dubstep", "house", "edm", "trance", "drum and bass", "future bass", "electro",
                   "techno", "garage", "breakbeat", "dance", "bass", "rave", "hardstyle")),
    ("electronic", ("electronic", "ambient", "idm", "downtempo", "trip hop", "glitch", "synth")),
    ("hip hop", ("hip hop", "rap", "trap", "drill", "grime", "boom bap")),
    ("r&b/soul", ("r&b", "soul", "funk", "disco", "motown", "new jack")),
    ("jazz/blues", ("jazz", "bop", "swing", "blues")),
    ("classical/score", ("classical", "opera", "minimalism", "score", "soundtrack", "orchestral",
                         "video game")),
    ("country/folk", ("country", "folk", "americana", "bluegrass", "singer-songwriter")),
    ("gospel/christian", ("gospel", "christian")),
    ("rock", ("rock", "punk", "grunge", "shoegaze", "emo", "post-", "new wave", "britpop")),
    ("pop", ("pop",)),
]


def genre_family(genre: str) -> str:
    for family, keys in GENRE_FAMILIES:
        if any(k in genre for k in keys):
            return family
    return "other"


def stub_expansion_seeds(
    conn: sqlite3.Connection, count: int, *, min_links: int = 2
) -> list[ArtistSeed]:
    """Pick stubs to fully collect, favoring gaps in genre coverage.

    score(stub) = sum over collected artists A linking to it of
                  similarity(A, stub) / share(family of A's top genre)

    Genres are grouped into broad families (rock, pop, edm/dance, latin, ...).
    A link from a synthwave artist counts for far more than one from a rock
    artist, because rock is already well covered. Stubs need at least
    `min_links` incoming links, so one-off neighbours don't dominate.
    """
    top_genre: dict[int, str] = {}
    for artist_id, genre in conn.execute(
        """SELECT ag.artist_id, ag.genre FROM artist_genres ag
           JOIN artists a ON a.id = ag.artist_id AND a.is_canonical = 1
           ORDER BY ag.artist_id, ag.weight DESC, ag.votes DESC"""
    ):
        top_genre.setdefault(artist_id, genre_family(genre))
    freq = Counter(top_genre.values())
    total = max(1, sum(freq.values()))

    links = conn.execute(
        """SELECT s.source_artist_id, s.target_artist_id, s.score, t.mbid, t.name
           FROM artist_similarities s
           JOIN artists src ON src.id = s.source_artist_id AND src.is_canonical = 1
           JOIN artists t ON t.id = s.target_artist_id
           WHERE t.is_canonical = 0 AND t.mbid IS NOT NULL"""
    ).fetchall()
    # Normalize provider scores per source so prolific sources don't dominate.
    max_score: dict[int, float] = defaultdict(float)
    for src, _, score, _, _ in links:
        max_score[src] = max(max_score[src], float(score or 0))

    score: dict[str, float] = defaultdict(float)
    n_links: Counter[str] = Counter()
    names: dict[str, str] = {}
    via: dict[str, Counter[str]] = defaultdict(Counter)
    for src, _, s, mbid, name in links:
        genre = top_genre.get(src)
        share = (freq[genre] / total) if genre else 1.0
        rel = (float(s or 0) / max_score[src]) if max_score[src] else 1.0
        score[mbid] += max(rel, 0.05) / share
        n_links[mbid] += 1
        names[mbid] = name
        if genre:
            via[mbid][genre] += 1

    ranked = sorted((m for m in score if n_links[m] >= min_links), key=score.get, reverse=True)
    seeds = []
    for mbid in ranked[:count]:
        genres = ", ".join(g for g, _ in via[mbid].most_common(2)) or "?"
        seeds.append(ArtistSeed(
            name=names[mbid],
            mbid=mbid,
            reason=(f"Stub expansion: {n_links[mbid]} links from collected artists "
                    f"(mostly {genres}), gap score {score[mbid]:.2f}"),
        ))
    return seeds
