"""Generate golden fixtures for the TypeScript port from the Python API.

    python scripts/gen_api_fixtures.py [bundle_dir] [out_path]

Writes a list of {function, args, expected}. Function names are the Python
snake_case names; the TS test maps them to camelCase. Skips get_preview_url
(network) and seedless random_artists (nondeterministic).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from music_data.api import MusicData

UNKNOWN = "00000000-0000-0000-0000-000000000000"


def _plain(x):
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, np.ndarray):
        return [float(v) for v in x.tolist()]
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if hasattr(x, "to_dict"):
        return _plain(x.to_dict())
    raise TypeError(type(x))


def build(bundle: Path) -> list[dict]:
    md = MusicData(bundle)
    db = sqlite3.connect(bundle / "music.db")
    one = lambda sql: db.execute(sql).fetchone()[0]
    popular = one("SELECT mbid FROM artists WHERE in_catalog=1 ORDER BY popularity DESC, mbid LIMIT 1")
    second = one("SELECT mbid FROM artists WHERE in_catalog=1 ORDER BY popularity DESC, mbid LIMIT 1 OFFSET 1")
    no_audio = one("SELECT mbid FROM artists WHERE in_catalog=1 AND has_audio=0 ORDER BY mbid LIMIT 1")
    no_genre = one("SELECT mbid FROM artists WHERE in_catalog=1 AND has_genre=0 ORDER BY mbid LIMIT 1")
    stub = one("SELECT mbid FROM artists WHERE in_catalog=0 ORDER BY mbid LIMIT 1")
    stub_name = one(f"SELECT name FROM artists WHERE mbid='{stub}'")
    top_genre = md.list_genres()[0].genre
    taste = md.get_vector(popular, "audio") + md.get_vector(second, "audio")

    cases: list[tuple[str, list]] = [
        ("dataset_info", []),
        ("list_genres", []),
        ("artists_by_genre", [top_genre, 25]),
        ("artists_by_genre", ["no such genre", 10]),
        ("get_artist", [popular]),
        ("get_artist", [no_audio]),
        ("get_artist", [stub]),
        ("get_artist", [UNKNOWN]),
        ("get_artists", [[popular, stub, UNKNOWN]]),
        ("get_artist_tracks", [popular]),
        ("get_artist_tracks", [stub]),
        ("get_artist_tracks", [UNKNOWN]),
        ("similarity_between", [popular, second]),
        ("similarity_between", [popular, no_audio]),
        ("similarity_between", [popular, stub]),
        ("nearest_to_vector", [[float(v) for v in taste], "audio", 15, [popular, second]]),
        ("nearest_to_vector", [[1.0, 2.0], "audio", 5, []]),
        ("get_vector", [popular, "audio"]),
        ("get_vector", [popular, "text"]),
        ("get_vector", [no_audio, "audio"]),
        ("random_artists", [5, None, 42]),
        ("random_artists", [5, top_genre, 7]),
        ("resolve_artist", ["radiohead"]),
        ("resolve_artist", ["zzzzqqqq"]),
    ]
    for sig in ("listeners", "audio", "text", "genre"):
        cases.append(("similar_artists", [popular, sig, 15]))
        cases.append(("similar_artists", [no_audio, sig, 10]))
    cases.append(("similar_artists", [no_genre, "genre", 10]))
    cases.append(("similar_artists", [stub, "audio", 10]))
    cases.append(("similar_artists", [UNKNOWN, "listeners", 10]))
    for q in ["Radiohead", "radiohed", "bjork", "beyonce", "the beat", "sigur ros", "", stub_name]:
        cases.append(("search_artists", [q, 10, True]))
    cases.append(("search_artists", ["the", 15, False]))

    return [{"function": f, "args": a, "expected": _plain(getattr(md, f)(*a))} for f, a in cases]


def main() -> None:
    bundle = Path(sys.argv[1] if len(sys.argv) > 1 else "api_bundle")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "ts/test/fixtures.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    fixtures = build(bundle)
    out.write_text(json.dumps(fixtures, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {len(fixtures)} fixtures -> {out}")


if __name__ == "__main__":
    main()
