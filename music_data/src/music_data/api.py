"""Read-only music data API over a published bundle.

    from music_data.api import MusicData
    md = MusicData("api_bundle")
    md.search_artists("radiohead")

Depends only on the standard library and numpy. It reads the bundle written by
``python -m music_data publish`` and never touches the pipeline database. The
only network call is :meth:`MusicData.get_preview_url`.

Rules: artists are keyed by MBID. Unknown input returns ``None`` or ``[]`` and
never raises. Scores are comparable only within one signal.

The TypeScript port in ``ts/`` mirrors this file function for function; keep
scoring, rounding and tie-break rules identical in both.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import unicodedata
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SIGNALS = ("listeners", "audio", "text", "genre")
VECTOR_SIGNALS = ("audio", "text")
PREVIEW_TTL_SECONDS = 600
DEEZER_TRACK_URL = "https://api.deezer.com/track/{}"


# ----------------------------------------------------------------------
# Return types
# ----------------------------------------------------------------------

class _Dict:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Genre(_Dict):
    genre: str
    weight: float


@dataclass(frozen=True)
class Artist(_Dict):
    mbid: str
    name: str
    in_catalog: bool
    sort_name: str | None = None
    artist_type: str | None = None
    country: str | None = None
    begin_year: int | None = None
    end_year: int | None = None
    gender: str | None = None
    popularity: float | None = None
    popularity_percentile: float | None = None
    has_audio: bool = False
    has_text: bool = False
    has_listeners: bool = False
    has_genre: bool = False
    aliases: tuple[str, ...] = ()
    genres: tuple[Genre, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["aliases"] = list(self.aliases)
        d["genres"] = [g.to_dict() for g in self.genres]
        return d


@dataclass(frozen=True)
class SearchResult(_Dict):
    mbid: str
    name: str
    in_catalog: bool
    score: float
    matched: str


@dataclass(frozen=True)
class Track(_Dict):
    id: int
    recording_mbid: str | None
    title: str
    artist_mbid: str
    duration_ms: int | None
    release_date: str | None
    has_preview: bool


@dataclass(frozen=True)
class SimilarArtist(_Dict):
    mbid: str
    name: str
    score: float
    signal: str


@dataclass(frozen=True)
class GenreCount(_Dict):
    genre: str
    artist_count: int


@dataclass(frozen=True)
class GenreArtist(_Dict):
    mbid: str
    name: str
    weight: float


@dataclass(frozen=True)
class Similarity(_Dict):
    mbid_a: str
    mbid_b: str
    scores: dict[str, float | None] = field(default_factory=dict)
    shared_genres: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mbid_a": self.mbid_a,
            "mbid_b": self.mbid_b,
            "scores": dict(self.scores),
            "shared_genres": list(self.shared_genres),
        }


@dataclass(frozen=True)
class Resolution(_Dict):
    mbid: str
    name: str
    in_catalog: bool
    confidence: float


# ----------------------------------------------------------------------
# Helpers shared with the TS port (keep in sync)
# ----------------------------------------------------------------------

def normalize_name(s: str) -> str:
    """Lowercase, strip accents, keep letters/digits, collapse spaces."""
    s = unicodedata.normalize("NFKD", s)
    out = []
    for ch in s:
        if unicodedata.combining(ch):
            continue
        out.append(ch.lower() if ch.isalnum() else " ")
    return " ".join("".join(out).split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def match_score(q: str, name: str) -> float:
    """Score a normalised query against a normalised name (0 = no match)."""
    if not q or not name:
        return 0.0
    if name == q:
        return 1.0
    if name.startswith(q):
        return 0.9
    if (" " + q) in (" " + name):
        return 0.8
    if q in name:
        return 0.7
    if len(q) >= 3 and abs(len(q) - len(name)) <= 2:
        d = levenshtein(q, name)
        if d <= max(1, len(q) // 4):
            return round(0.6 * (1 - d / max(len(q), len(name))), 6)
    return 0.0


def _round(x: float) -> float:
    return round(float(x), 6)


class Mulberry32:
    """Tiny seeded PRNG, bit-identical with the TS port."""

    def __init__(self, seed: int) -> None:
        self.a = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self.a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t ^= (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def _sort_key_pop(pop: float | None) -> float:
    return -(pop if pop is not None else -1.0)


# ----------------------------------------------------------------------
# Main class
# ----------------------------------------------------------------------

class MusicData:
    """Read-only access to a published music data bundle."""

    def __init__(self, bundle_dir: str | Path) -> None:
        self.bundle_dir = Path(bundle_dir)
        db_path = self.bundle_dir / "music.db"
        if not db_path.exists():
            raise FileNotFoundError(f"No music.db in bundle: {self.bundle_dir}")
        self._conn = sqlite3.connect(
            f"file:{db_path.as_posix()}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._manifest = json.loads((self.bundle_dir / "manifest.json").read_text(encoding="utf-8"))

        # Name index for search: (normalised name, mbid, display string, is_alias)
        self._names: list[tuple[str, str, str, bool]] = []
        self._artist_rows: dict[str, sqlite3.Row] = {}
        for r in self._conn.execute("SELECT * FROM artists ORDER BY rowid"):
            self._artist_rows[r["mbid"]] = r
            self._names.append((normalize_name(r["name"]), r["mbid"], r["name"], False))
        for r in self._conn.execute("SELECT artist_mbid, name FROM aliases ORDER BY rowid"):
            self._names.append((normalize_name(r["name"]), r["artist_mbid"], r["name"], True))

        self._vectors: dict[str, tuple[np.ndarray, list[str], dict[str, int]]] = {}
        self._genre_vecs: dict[str, dict[str, float]] | None = None
        self._preview_cache: dict[int, tuple[float, str | None]] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> MusicData:  # noqa: PYI034
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------- internal ----------------

    def _catalog(self, mbid: str) -> sqlite3.Row | None:
        r = self._artist_rows.get(mbid) if isinstance(mbid, str) else None
        return r if r is not None and r["in_catalog"] else None

    def _load_vectors(self, signal: str) -> tuple[np.ndarray, list[str], dict[str, int]]:
        with self._lock:
            if signal not in self._vectors:
                mat = np.load(self.bundle_dir / f"{signal}.f16.npy").astype(np.float64)
                ids = json.loads((self.bundle_dir / f"{signal}_ids.json").read_text(encoding="utf-8"))
                self._vectors[signal] = (mat, ids, {m: i for i, m in enumerate(ids)})
            return self._vectors[signal]

    def _genre_map(self) -> dict[str, dict[str, float]]:
        if self._genre_vecs is None:
            g: dict[str, dict[str, float]] = {}
            for r in self._conn.execute("SELECT artist_mbid, genre, weight FROM genres"):
                g.setdefault(r["artist_mbid"], {})[r["genre"]] = r["weight"]
            self._genre_vecs = g
        return self._genre_vecs

    @staticmethod
    def _genre_cos(a: dict[str, float], b: dict[str, float]) -> float:
        dot = sum(w * b[k] for k, w in a.items() if k in b)
        if dot == 0:
            return 0.0
        na = sum(w * w for w in a.values()) ** 0.5
        nb = sum(w * w for w in b.values()) ** 0.5
        return dot / (na * nb)

    def _listener_edges(self, mbid: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in self._conn.execute(
            "SELECT target_mbid AS other, score FROM similarity_edges WHERE source_mbid = ? "
            "UNION ALL SELECT source_mbid AS other, score FROM similarity_edges WHERE target_mbid = ?",
            (mbid, mbid),
        ):
            if r["other"] != mbid and r["score"] is not None:
                out[r["other"]] = max(out.get(r["other"], 0.0), float(r["score"]))
        if not out:
            return {}
        top = max(out.values())
        return {k: v / top for k, v in out.items()}

    def _ranked(self, scores: Iterable[tuple[str, float]], signal: str, limit: int) -> list[SimilarArtist]:
        rows = []
        for m, s in scores:
            r = self._catalog(m)
            if r is not None:
                rows.append((-_round(s), m, r["name"]))
        rows.sort()
        return [SimilarArtist(m, n, -s, signal) for s, m, n in rows[: max(0, int(limit))]]

    def _to_artist(self, r: sqlite3.Row) -> Artist:
        if not r["in_catalog"]:
            return Artist(mbid=r["mbid"], name=r["name"], in_catalog=False)
        aliases = tuple(
            x["name"] for x in self._conn.execute(
                "SELECT DISTINCT name FROM aliases WHERE artist_mbid = ? ORDER BY name", (r["mbid"],)
            )
        )
        genres = tuple(
            Genre(x["genre"], x["weight"]) for x in self._conn.execute(
                "SELECT genre, weight FROM genres WHERE artist_mbid = ? ORDER BY weight DESC, genre",
                (r["mbid"],),
            )
        )
        return Artist(
            mbid=r["mbid"], name=r["name"], in_catalog=True,
            sort_name=r["sort_name"], artist_type=r["artist_type"], country=r["country"],
            begin_year=r["begin_year"], end_year=r["end_year"], gender=r["gender"],
            popularity=r["popularity"], popularity_percentile=r["popularity_percentile"],
            has_audio=bool(r["has_audio"]), has_text=bool(r["has_text"]),
            has_listeners=bool(r["has_listeners"]), has_genre=bool(r["has_genre"]),
            aliases=aliases, genres=genres,
        )

    # ---------------- search ----------------

    def search_artists(self, query: str, limit: int = 20, include_uncatalogued: bool = True) -> list[SearchResult]:
        q = normalize_name(query) if isinstance(query, str) else ""
        if not q or limit <= 0:
            return []
        best: dict[str, tuple[float, str]] = {}
        for norm, mbid, display, is_alias in self._names:
            s = match_score(q, norm)
            if s <= 0:
                continue
            if is_alias:
                s = _round(s * 0.95)
            if mbid not in best or s > best[mbid][0]:
                best[mbid] = (s, display)
        rows = []
        for mbid, (s, display) in best.items():
            r = self._artist_rows.get(mbid)
            if r is None or (not include_uncatalogued and not r["in_catalog"]):
                continue
            rows.append((-r["in_catalog"], -s, _sort_key_pop(r["popularity"]), mbid, r, s, display))
        rows.sort(key=lambda t: t[:4])
        return [
            SearchResult(mbid=t[3], name=t[4]["name"], in_catalog=bool(t[4]["in_catalog"]), score=t[5], matched=t[6])
            for t in rows[:limit]
        ]

    def resolve_artist(self, name: str) -> Resolution | None:
        hits = self.search_artists(name, limit=1)
        if not hits or hits[0].score < 0.5:
            return None
        h = hits[0]
        return Resolution(h.mbid, h.name, h.in_catalog, h.score)

    # ---------------- artists ----------------

    def get_artist(self, mbid: str) -> Artist | None:
        r = self._artist_rows.get(mbid) if isinstance(mbid, str) else None
        return self._to_artist(r) if r is not None else None

    def get_artists(self, mbids: Sequence[str]) -> list[Artist | None]:
        return [self.get_artist(m) for m in mbids]

    def get_artist_tracks(self, mbid: str) -> list[Track]:
        if self._catalog(mbid) is None:
            return []
        rows = self._conn.execute(
            """
            SELECT DISTINCT t.id, t.recording_mbid, t.title, t.artist_mbid, t.duration_ms,
                   t.release_date, t.has_preview
            FROM tracks t
            WHERE t.artist_mbid = ?
               OR t.id IN (SELECT recording_id FROM track_artists WHERE artist_mbid = ?)
            ORDER BY t.release_date IS NULL, t.release_date, t.title, t.id
            """,
            (mbid, mbid),
        )
        return [
            Track(r["id"], r["recording_mbid"], r["title"], r["artist_mbid"], r["duration_ms"],
                  r["release_date"], bool(r["has_preview"]))
            for r in rows
        ]

    # ---------------- similarity ----------------

    def similar_artists(self, mbid: str, signal: str, limit: int = 20) -> list[SimilarArtist]:
        if signal not in SIGNALS or self._catalog(mbid) is None:
            return []
        if signal == "listeners":
            return self._ranked(self._listener_edges(mbid).items(), signal, limit)
        if signal == "genre":
            gm = self._genre_map()
            mine = gm.get(mbid)
            if not mine:
                return []
            scores = ((m, self._genre_cos(mine, g)) for m, g in gm.items() if m != mbid)
            return self._ranked(((m, s) for m, s in scores if s > 0), signal, limit)
        mat, ids, index = self._load_vectors(signal)
        if mbid not in index:
            return []
        sims = mat @ mat[index[mbid]]
        return self._ranked(((ids[i], float(s)) for i, s in enumerate(sims) if ids[i] != mbid), signal, limit)

    def similarity_between(self, mbid_a: str, mbid_b: str) -> Similarity | None:
        if self._catalog(mbid_a) is None or self._catalog(mbid_b) is None:
            return None
        scores: dict[str, float | None] = {}
        edges = self._listener_edges(mbid_a)
        scores["listeners"] = _round(edges[mbid_b]) if mbid_b in edges else None
        for sig in VECTOR_SIGNALS:
            mat, _ids, index = self._load_vectors(sig)
            if mbid_a in index and mbid_b in index:
                scores[sig] = _round(float(mat[index[mbid_a]] @ mat[index[mbid_b]]))
            else:
                scores[sig] = None
        gm = self._genre_map()
        ga, gb = gm.get(mbid_a), gm.get(mbid_b)
        scores["genre"] = _round(self._genre_cos(ga, gb)) if ga and gb else None
        shared: tuple[str, ...] = ()
        if ga and gb:
            common = [(-min(ga[k], gb[k]), k) for k in ga if k in gb]
            shared = tuple(k for _w, k in sorted(common))
        return Similarity(mbid_a, mbid_b, scores, shared)

    def nearest_to_vector(
        self, vector: Sequence[float] | np.ndarray, signal: str, limit: int = 20, exclude: Iterable[str] = ()
    ) -> list[SimilarArtist]:
        if signal not in VECTOR_SIGNALS:
            return []
        mat, ids, _index = self._load_vectors(signal)
        try:
            v = np.asarray(vector, dtype=np.float64).ravel()
        except (TypeError, ValueError):
            return []
        if v.shape[0] != mat.shape[1] or not np.all(np.isfinite(v)):
            return []
        n = float(np.linalg.norm(v))
        if n == 0:
            return []
        sims = mat @ (v / n)
        skip = set(exclude)
        return self._ranked(((ids[i], float(s)) for i, s in enumerate(sims) if ids[i] not in skip), signal, limit)

    def get_vector(self, mbid: str, signal: str) -> np.ndarray | None:
        if signal not in VECTOR_SIGNALS or not isinstance(mbid, str):
            return None
        mat, _ids, index = self._load_vectors(signal)
        i = index.get(mbid)
        return None if i is None else mat[i].astype(np.float32)

    # ---------------- genres / sampling ----------------

    def list_genres(self) -> list[GenreCount]:
        return [
            GenreCount(r["genre"], r["n"]) for r in self._conn.execute(
                "SELECT g.genre, COUNT(DISTINCT g.artist_mbid) AS n FROM genres g "
                "JOIN artists a ON a.mbid = g.artist_mbid AND a.in_catalog = 1 "
                "GROUP BY g.genre ORDER BY n DESC, g.genre"
            )
        ]

    def artists_by_genre(self, genre: str, limit: int = 50) -> list[GenreArtist]:
        if not isinstance(genre, str) or limit <= 0:
            return []
        return [
            GenreArtist(r["mbid"], r["name"], r["weight"]) for r in self._conn.execute(
                "SELECT a.mbid, a.name, g.weight FROM genres g "
                "JOIN artists a ON a.mbid = g.artist_mbid AND a.in_catalog = 1 "
                "WHERE g.genre = ? "
                "ORDER BY g.weight DESC, COALESCE(a.popularity, -1) DESC, a.mbid LIMIT ?",
                (genre.strip().lower(), int(limit)),
            )
        ]

    def random_artists(self, n: int, genre: str | None = None, seed: int | None = None) -> list[Artist]:
        if n <= 0:
            return []
        if genre is None:
            pool = [r[0] for r in self._conn.execute("SELECT mbid FROM artists WHERE in_catalog = 1 ORDER BY mbid")]
        else:
            pool = [r[0] for r in self._conn.execute(
                "SELECT DISTINCT g.artist_mbid FROM genres g JOIN artists a ON a.mbid = g.artist_mbid "
                "AND a.in_catalog = 1 WHERE g.genre = ? ORDER BY g.artist_mbid",
                (genre.strip().lower(),),
            )]
        if seed is None:
            seed = int(time.time_ns()) & 0xFFFFFFFF
        rng = Mulberry32(int(seed))
        k = min(int(n), len(pool))
        for i in range(k):  # partial Fisher-Yates
            j = i + int(rng.next() * (len(pool) - i))
            pool[i], pool[j] = pool[j], pool[i]
        return [self._to_artist(self._artist_rows[m]) for m in pool[:k]]

    # ---------------- network ----------------

    def get_preview_url(self, track_id: int) -> str | None:
        """Fresh 30s Deezer preview URL for a bundle track id, or None."""
        try:
            tid = int(track_id)
        except (TypeError, ValueError):
            return None
        now = time.monotonic()
        hit = self._preview_cache.get(tid)
        if hit and now - hit[0] < PREVIEW_TTL_SECONDS:
            return hit[1]
        r = self._conn.execute("SELECT deezer_id FROM tracks WHERE id = ?", (tid,)).fetchone()
        if r is None or not r["deezer_id"]:
            return None
        url: str | None = None
        try:
            req = urllib.request.Request(
                DEEZER_TRACK_URL.format(r["deezer_id"]), headers={"User-Agent": "music_data-api"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                url = json.loads(resp.read().decode("utf-8")).get("preview") or None
        except Exception:  # noqa: BLE001 - network errors must never raise
            return None
        self._preview_cache[tid] = (now, url)
        return url

    # ---------------- info ----------------

    def dataset_info(self) -> dict[str, Any]:
        m = self._manifest
        return {
            "bundle_version": m.get("bundle_version"),
            "build_time": m.get("build_time"),
            "catalog_artists": m["artists"]["complete"],
            "uncatalogued_artists": m["artists"]["stub"],
            "tracks": m.get("tracks"),
            "genres": len(self.list_genres()),
            "audio_vectors": m["embeddings"]["audio"]["count"],
            "text_vectors": m["embeddings"]["text"]["count"],
        }
