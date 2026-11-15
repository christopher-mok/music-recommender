"""Compare audio embedding specs stored in a DB on a small labelled sample.

Usage:
    python scripts/compare_audio_models.py data/sample_compare.db \
        --genre synthwave "The Midnight" "Kidburn" \
        --genre melodic_dubstep "Seven Lions" "ILLENIUM" "Said the Sky" \
        --output reports/audio_model_comparison.md

Metrics (track level, each clip = one L2-normalised mean-of-windows vector):
  genre P@k   share of a track's k nearest neighbours with the same genre
  artist P@k  share of neighbours sharing at least one credited artist
  margin      mean same-genre cosine - mean cross-genre cosine
  sil         silhouette score by genre (cosine distance)
Each model is also scored after subtracting the sample mean vector
("mean-centred"), which removes the shared direction many embedders have.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_data.embeddings.blob_helpers import blob_to_array  # noqa: E402


def load(conn, spec_hash, dim):
    rows = conn.execute(
        """
        SELECT e.input_hash, e.vectors, ac.track_id, r.title,
               GROUP_CONCAT(a.name, '|') AS artists
        FROM embeddings e
        JOIN audio_clips ac ON ac.audio_sha256 = e.input_hash
        JOIN recordings r ON r.id = ac.track_id
        JOIN recording_artists ra ON ra.recording_id = ac.track_id
        JOIN artists a ON a.id = ra.artist_id AND a.is_canonical = 1
        WHERE e.spec_hash = ?
        GROUP BY e.input_hash ORDER BY e.input_hash
        """,
        (spec_hash,),
    ).fetchall()
    X, meta = [], []
    for h, blob, tid, title, artists in rows:
        v = blob_to_array(blob).reshape(-1, dim).mean(axis=0)
        X.append(v / max(np.linalg.norm(v), 1e-12))
        meta.append({"hash": h, "title": title, "artists": set(artists.split("|"))})
    return np.stack(X), meta


def silhouette(S, labels):
    D = 1 - S
    labels = np.asarray(labels)
    scores = []
    for i in range(len(labels)):
        same = (labels == labels[i]); same[i] = False
        other = labels != labels[i]
        if not same.any() or not other.any():
            continue
        a, b = D[i, same].mean(), D[i, other].mean()
        scores.append((b - a) / max(a, b))
    return float(np.mean(scores))


def evaluate(X, meta, genre_of, k):
    S = X @ X.T
    np.fill_diagonal(S, -np.inf)
    genres = [genre_of(m["artists"]) for m in meta]
    g_hits, a_hits = [], []
    for i in range(len(X)):
        nn = np.argsort(-S[i])[:k]
        g_hits.append(np.mean([genres[j] == genres[i] for j in nn]))
        a_hits.append(np.mean([bool(meta[j]["artists"] & meta[i]["artists"]) for j in nn]))
    np.fill_diagonal(S, np.nan)
    G = np.array(genres)
    same = (G[:, None] == G[None, :])
    off = ~np.eye(len(G), dtype=bool)
    within = np.nanmean(S[same & off]); across = np.nanmean(S[~same])
    np.fill_diagonal(S, 1.0)
    return {
        "n_tracks": len(X),
        f"genre_P@{k}": float(np.mean(g_hits)),
        f"artist_P@{k}": float(np.mean(a_hits)),
        "within_genre_cos": float(within),
        "cross_genre_cos": float(across),
        "margin": float(within - across),
        "silhouette": silhouette(S, genres),
    }, S, genres


def artist_matrix(conn, spec_hash, dim, names):
    vecs = {}
    for name in names:
        row = conn.execute(
            """SELECT av.vector FROM artist_vectors av JOIN artists a ON a.mbid = av.mbid
               WHERE av.spec_hash = ? AND av.pooling = 'mean' AND a.name = ?""",
            (spec_hash, name)).fetchone()
        if row:
            vecs[name] = blob_to_array(row[0]).reshape(-1)[:dim]
    ns = list(vecs)
    M = np.array([[float(vecs[a] @ vecs[b]) for b in ns] for a in ns])
    return ns, M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--genre", nargs="+", action="append", required=True,
                    metavar=("LABEL", "ARTIST"))
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    artist_genre = {a: g[0] for g in args.genre for a in g[1:]}
    names = [a for g in args.genre for a in g[1:]]

    def genre_of(artists):
        gs = {artist_genre[a] for a in artists if a in artist_genre}
        return gs.pop() if len(gs) == 1 else "mixed"

    conn = sqlite3.connect(args.db)
    specs = conn.execute(
        "SELECT spec_hash, model_name, dim, spec_json FROM embedding_specs "
        "WHERE modality = 'audio' ORDER BY model_name").fetchall()

    out = ["# Audio embedding model comparison", "",
           f"DB: `{args.db}` — genres: " +
           "; ".join(f"**{g[0]}**: {', '.join(g[1:])}" for g in args.genre), ""]
    summary = []
    for sh, model, dim, spec_json in specs:
        X, meta = load(conn, sh, dim)
        if len(X) < 3:
            continue
        res, S, genres = evaluate(X, meta, genre_of, args.k)
        Xc = X - X.mean(axis=0)
        Xc /= np.linalg.norm(Xc, axis=1, keepdims=True).clip(min=1e-12)
        res_c, _, _ = evaluate(Xc, meta, genre_of, args.k)
        spec = json.loads(spec_json)
        summary.append((model, res))
        summary.append((model + " (mean-centred)", res_c))
        ns, M = artist_matrix(conn, sh, dim, names)
        out += [f"## {model}", "",
                f"`{spec['model']}@{spec['revision'][:10]}` · dim {dim} · "
                f"{spec['sample_rate']} Hz · {spec['window_seconds']}s windows · "
                f"spec `{sh[:12]}`", "",
                "Artist cosine (mean-pooled, L2):", "",
                "| | " + " | ".join(ns) + " |", "|---" * (len(ns) + 1) + "|"]
        out += [f"| {a} | " + " | ".join(f"{M[i, j]:.3f}" for j in range(len(ns))) + " |"
                for i, a in enumerate(ns)]
        # Worst tracks: nearest neighbour from the other genre
        S2 = S.copy(); np.fill_diagonal(S2, -np.inf)
        bad = []
        for i in range(len(X)):
            j = int(np.argmax(S2[i]))
            if genres[j] != genres[i] and "mixed" not in (genres[i], genres[j]):
                bad.append(f"- {meta[i]['title']} ({'/'.join(sorted(meta[i]['artists']))}) "
                           f"→ {meta[j]['title']} ({'/'.join(sorted(meta[j]['artists']))}) "
                           f"cos {S2[i, j]:.3f}")
        out += ["", f"Tracks whose nearest neighbour is the other genre: {len(bad)}"] + bad + [""]

    keys = list(summary[0][1]) if summary else []
    table = ["## Summary", "", "| model | " + " | ".join(keys) + " |",
             "|---" * (len(keys) + 1) + "|"]
    table += [f"| {m} | " + " | ".join(f"{r[k]:.3f}" if isinstance(r[k], float) else str(r[k])
                                       for k in keys) + " |" for m, r in summary]
    text = "\n".join(out[:4] + table + [""] + out[4:])
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
