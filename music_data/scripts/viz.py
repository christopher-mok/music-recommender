#!/usr/bin/env python3
"""
Standalone read-only web visualizer for the music_data SQLite database.

Usage:
    streamlit run scripts/viz.py              # auto-detects DB from config.py
    MUSIC_DATA_DB=data/music_metadata.db streamlit run scripts/viz.py

The viz uses its own cache directory under .cache/music_viz/ and writes
no files into the database or the pipeline's own .cache/ tree.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config: auto-detect DB from src/music_data/config.py
# ---------------------------------------------------------------------------

def _resolve_db_path() -> str:
    """Find the DB path by importing config.py. Falls back to env / default."""
    try:
        from music_data.config import load_config
        cfg = load_config()
        return cfg.database.path
    except Exception:
        pass
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from music_data.config import load_config
        cfg = load_config()
        return cfg.database.path
    except Exception:
        pass
    return os.environ.get("MUSIC_DATA_DB", "data/music_metadata_100.db")


DB_PATH = os.environ.get("MUSIC_DATA_DB", "") or _resolve_db_path()
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "music_viz"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Lazy import streamlit (safe for bare-mode test)
# ---------------------------------------------------------------------------

def _import_streamlit():
    """Import streamlit lazily so bare-mode imports don't trigger UI code."""
    try:
        import streamlit as st
    except ImportError:
        st = None  # type: ignore[assignment]
    return st


# ---------------------------------------------------------------------------
# DB helpers (read-only)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DB connection helper (true read-only; sees WAL data)
# ---------------------------------------------------------------------------

def get_ro_conn() -> sqlite3.Connection:
    """Return a read-only connection to the DB (mode=ro, query_only)."""
    uri = Path(DB_PATH).expanduser().resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def table_row_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    """Return {table: count} for every user table."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    result: Dict[str, int] = {}
    for r in rows:
        tname = r[0]
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM [{tname}]").fetchone()[0]
            result[tname] = cnt
        except Exception:
            result[tname] = -1
    return result


def table_schema(conn: sqlite3.Connection, table: str) -> List[Dict[str, Any]]:
    """Return column info for a table."""
    cols = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    return [
        {"cid": c[0], "name": c[1], "type": c[2],
         "notnull": bool(c[3]), "default": c[4], "pk": bool(c[5])}
        for c in cols
    ]


def query_table(
    conn: sqlite3.Connection,
    table: str,
    page: int = 1,
    page_size: int = 100,
    filters: Optional[Dict[str, str]] = None,
    search: Optional[str] = None,
) -> Tuple[pd.DataFrame, int]:
    """Fetch a page of rows with optional column filters and full-text search."""
    schema = table_schema(conn, table)
    col_names = [c["name"] for c in schema]
    col_types = {c["name"]: c["type"] for c in schema}

    wheres: List[str] = []
    params: List[Any] = []

    if filters:
        for col, val in filters.items():
            if val.strip():
                if col_types.get(col, "").upper() in ("INTEGER", "REAL"):
                    try:
                        wheres.append(f"[{col}] = ?")
                        params.append(float(val))
                    except ValueError:
                        pass
                else:
                    wheres.append(f"[{col}] LIKE ?")
                    params.append(f"%{val}%")

    if search:
        for col in col_names:
            wheres.append(f"[{col}] LIKE ?")
            params.append(f"%{search}%")

    where_sql = " WHERE " + " AND ".join(wheres) if wheres else ""

    total = conn.execute(f"SELECT COUNT(*) FROM [{table}]{where_sql}", params).fetchone()[0]
    total_pages = max(1, (total + page_size - 1) // page_size)

    offset = (page - 1) * page_size
    query = f"SELECT * FROM [{table}]{where_sql} LIMIT ? OFFSET ?"
    params.extend([page_size, offset])
    rows = conn.execute(query, params).fetchall()
    df = pd.DataFrame([dict(r) for r in rows], columns=col_names)
    return df, total_pages


# ---------------------------------------------------------------------------
# Blob helpers for embedding vectors
# ---------------------------------------------------------------------------

def _blob_to_array(blob_data: bytes) -> np.ndarray:
    """Decode: int32 ndim, int32 shape[ndim], then float32 data (see blob_helpers)."""
    if not blob_data or len(blob_data) < 4:
        return np.array([])
    ndim = int(np.frombuffer(blob_data[:4], dtype=np.int32)[0])
    if ndim < 1 or ndim > 4 or len(blob_data) < 4 + 4 * ndim:
        return np.array([])
    dims = np.frombuffer(blob_data[4:4 + 4 * ndim], dtype=np.int32).tolist()
    arr = np.frombuffer(blob_data[4 + 4 * ndim:], dtype=np.float32)
    try:
        return arr.reshape(dims)
    except ValueError:
        return np.array([])


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

def _projection_cache_path(spec_hash: str) -> Path:
    return CACHE_DIR / f"projection_{spec_hash}.npz"


def compute_projection(
    conn: sqlite3.Connection,
    spec_hash: str,
    use_umap: bool = True,
    max_points: int = 5000,
) -> Tuple[Optional[np.ndarray], List[Dict[str, Any]], str]:
    """Fetch track embeddings and project to 2D (UMAP -> t-SNE -> PCA)."""
    cache_path = _projection_cache_path(spec_hash)

    if cache_path.exists():
        try:
            data = np.load(str(cache_path))
            return data["points"], data["items"].tolist(), str(data.get("method_used", "cached"))
        except Exception:
            pass

    query = """
        SELECT e.entity_id AS rec_id, a.name AS artist, r.title, g.genre, e.vectors
        FROM embeddings e
        JOIN recording_artists ra ON ra.recording_id = CAST(e.entity_id AS INTEGER)
        JOIN artists a ON a.id = ra.artist_id
        JOIN recordings r ON r.id = CAST(e.entity_id AS INTEGER)
        LEFT JOIN (
            SELECT artist_id, genre, weight,
                   ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY weight DESC) AS rn
            FROM artist_genres
        ) g ON g.artist_id = ra.artist_id AND g.rn = 1
        WHERE e.entity_type = 'track' AND e.spec_hash = ?
    """

    all_rows = conn.execute(query, (spec_hash,)).fetchall()
    items: List[Dict[str, Any]] = []
    vectors_list: List[np.ndarray] = []

    for r in all_rows:
        vec = _blob_to_array(r["vectors"])
        if vec.size == 0 or vec.shape[0] == 0:
            continue
        vec_mean = vec.mean(axis=0) if vec.ndim == 2 else vec.ravel()
        items.append({"rec_id": int(r["rec_id"]), "artist": r["artist"],
                       "title": r["title"], "genre": r["genre"] or "unknown"})
        vectors_list.append(vec_mean)

    if not items:
        return None, [], "n/a"

    if len(items) > max_points:
        rng = np.random.RandomState(42)
        idx = sorted(rng.choice(len(items), max_points, replace=False))
        items = [items[i] for i in idx]
        vectors_list = [vectors_list[i] for i in idx]

    X = np.stack(vectors_list)
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms[norms == 0] = 1
    X = X / norms

    points: Optional[np.ndarray] = None
    method_used = "pca"

    if use_umap:
        try:
            import umap
            nn = max(2, min(30, len(X) - 1))
            points = umap.UMAP(n_components=2, n_neighbors=nn, min_dist=0.1,
                               metric="cosine", random_state=42).fit_transform(X)
            method_used = "umap"
        except Exception:
            pass

    if points is None:
        try:
            from sklearn.manifold import TSNE
            perp = max(2.0, min(30, len(X) - 3))
            points = TSNE(n_components=2, perplexity=perp, metric="cosine",
                          random_state=42, init="pca", learning_rate="auto").fit_transform(X)
            method_used = "tsne"
        except Exception:
            pass

    if points is None:
        from sklearn.decomposition import PCA
        points = PCA(n_components=2, random_state=42).fit_transform(X)
        method_used = "pca"

    try:
        np.savez_compressed(str(cache_path), points=points,
                            items=np.array(items, dtype=object), method_used=method_used)
    except Exception:
        pass

    return points, items, method_used


# ---------------------------------------------------------------------------
# Coverage stats helpers
# ---------------------------------------------------------------------------

def get_genre_coverage(conn: sqlite3.Connection) -> pd.DataFrame:
    return conn.execute("""
        SELECT g.genre,
               SUM(CASE WHEN a.collection_status = 'complete' THEN 1 ELSE 0 END) AS complete,
               SUM(CASE WHEN a.collection_status = 'partial' THEN 1 ELSE 0 END) AS partial,
               SUM(CASE WHEN a.collection_status = 'stub' THEN 1 ELSE 0 END) AS stub,
               COUNT(*) AS total
        FROM artist_genres g JOIN artists a ON a.id = g.artist_id
        GROUP BY g.genre ORDER BY total DESC LIMIT 50
    """).fetchall()


def get_clip_coverage(conn: sqlite3.Connection) -> Dict[str, Any]:
    total = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    with_clips = conn.execute("SELECT COUNT(DISTINCT track_id) FROM audio_clips").fetchone()[0]
    with_local = conn.execute("SELECT COUNT(DISTINCT track_id) FROM audio_clips WHERE local_path IS NOT NULL").fetchone()[0]
    no_clip = conn.execute("SELECT COUNT(*) FROM preview_misses").fetchone()[0]
    with_emb = conn.execute("SELECT COUNT(DISTINCT entity_id) FROM embeddings WHERE entity_type='track'").fetchone()[0]
    return {"total": total, "with_clips": with_clips, "with_local": with_local,
            "no_clip": no_clip, "with_embedding": with_emb}


def get_provider_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = conn.execute("""
        SELECT provider, COUNT(*) AS cnt, SUM(from_cache) AS cached,
               COUNT(*) - SUM(from_cache) AS live
        FROM fetch_log GROUP BY provider ORDER BY cnt DESC
    """).fetchall()
    if not rows:
        return pd.DataFrame(columns=["provider", "cnt", "cached", "live"])
    return pd.DataFrame([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Genre quotas loading
# ---------------------------------------------------------------------------

def load_genre_quotas_gaps() -> List[Dict[str, Any]]:
    toml_path = Path(__file__).resolve().parent.parent / "data" / "genre_quotas_gaps.toml"
    slots: List[Dict[str, Any]] = []
    try:
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        default_count = data.get("default_count", 15)
        for slot in data.get("slots", []):
            slots.append({"name": slot.get("name", ""),
                          "query": slot.get("query", ""),
                          "count": slot.get("count", default_count)})
    except Exception:
        pass
    return slots


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

_GENRE_COLORS: Dict[str, str] = {}
_GENRE_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
]


def _genre_color(genre: str) -> str:
    if genre not in _GENRE_COLORS:
        idx = len(_GENRE_COLORS) % len(_GENRE_PALETTE)
        _GENRE_COLORS[genre] = _GENRE_PALETTE[idx]
    return _GENRE_COLORS[genre]


# ===================================================================
# APP ENTRY POINT
# ===================================================================

def _run_app() -> None:
    """Run the Streamlit application. Safe to call when st is None."""
    st = _import_streamlit()
    if st is None:
        print("Streamlit not available; running in test/inspection mode.")
        return

    # ---- Page config ----
    st.set_page_config(page_title="Music Data Visualizer", layout="wide", page_icon="🎵")
    st.sidebar.title("🎵 Music Data Viz")

    auto_refresh = st.sidebar.checkbox("Auto-refresh (real-time)", value=True,
                                        help="Re-query the database on each interaction")
    db_path_display = DB_PATH
    st.sidebar.markdown(f"**Database**: `{db_path_display}`")

    db_file = Path(DB_PATH).expanduser()
    if not db_file.exists():
        st.error(f"Database not found: `{DB_PATH}`")
        st.stop()

    conn_cached: Optional[sqlite3.Connection] = None

    def _get_conn() -> sqlite3.Connection:
        nonlocal conn_cached
        if conn_cached is not None and not auto_refresh:
            return conn_cached
        try:
            conn_cached = get_ro_conn()
            return conn_cached
        except Exception as e:
            st.error(f"Failed to open database: {e}")
            st.stop()
            raise RuntimeError("unreachable") from e

    # ---- Tabs ----
    tab_browser, tab_coverage, tab_embeddings, tab_audio = st.tabs([
        "📋 Table Browser", "📊 Coverage Stats",
        "🗺️ Embedding Map", "🎧 Audio Playback",
    ])

    # ===================================================================
    # TAB 1: Table Browser
    # ===================================================================
    with tab_browser:
        st.header("Table Browser")
        st.caption("Read-only view of all tables. Paginated with column filters.")
        conn = _get_conn()
        counts = table_row_counts(conn)
        table_names = sorted([t for t in counts if counts[t] >= 0])

        if not table_names:
            st.info("No tables found.")
        else:
            st.subheader("Tables Overview")
            info_df = pd.DataFrame([{"Table": t, "Rows": counts[t]} for t in table_names])
            st.dataframe(info_df, width="stretch", hide_index=True)

            selected_table = st.selectbox("Select table", options=table_names)

            if selected_table:
                schema = table_schema(conn, selected_table)
                col_names = [c["name"] for c in schema]
                with st.expander("📐 Schema"):
                    st.dataframe(pd.DataFrame(schema), width="stretch", hide_index=True)

                c1, c2, c3 = st.columns([2, 1, 1])
                with c1:
                    page_size = st.selectbox("Rows per page", [25, 50, 100, 200, 500], index=2)
                with c2:
                    total_rows = counts[selected_table]
                    total_pages = max(1, (total_rows + page_size - 1) // page_size)
                    page_num = st.number_input("Page", min_value=1, max_value=total_pages, value=1)
                with c3:
                    search_text = st.text_input("Search all columns", placeholder="Type to search...")

                st.markdown("### Column Filters")
                filters: Dict[str, str] = {}
                filter_cols = st.columns(4)
                for i in range(4):
                    col_name = st.selectbox(f"Column {i+1}", ["(none)"] + col_names, key=f"filter_col_{i}")
                    if col_name != "(none)":
                        val = st.text_input(f"Value for {col_name}", key=f"filter_val_{i}")
                        if val.strip():
                            filters[col_name] = val

                df, n_pages = query_table(conn, selected_table, page_num, page_size, filters, search_text)
                st.markdown(f"**{len(df)}** rows shown (of **{total_rows}** total, page {page_num}/{n_pages})")

                if not df.empty:
                    display_df = df.copy()
                    for col in display_df.columns:
                        if str(display_df[col].dtype) == "object":
                            sample = display_df[col].dropna().iloc[0] if len(display_df[col].dropna()) > 0 else None
                            if isinstance(sample, (bytes, bytearray)):
                                display_df[col] = display_df[col].apply(
                                    lambda x: x.hex()[:40] + "..." if isinstance(x, (bytes, bytearray)) and len(x) > 40
                                              else (x.hex() if isinstance(x, (bytes, bytearray)) else x))
                            elif isinstance(sample, (dict, list)):
                                display_df[col] = display_df[col].apply(
                                    lambda x: json.dumps(x) if isinstance(x, (dict, list)) else x)
                    st.dataframe(display_df, width="stretch", height=400)
                else:
                    st.info("No matching rows.")

    # ===================================================================
    # TAB 2: Coverage Stats
    # ===================================================================
    with tab_coverage:
        st.header("Coverage Statistics")
        st.caption("Artist/track coverage, clip/embedding gaps, and fetch provenance.")
        conn = _get_conn()

        st.subheader("Overall Summary")
        stats_grid = st.columns(5)
        with stats_grid[0]:
            st.metric("Artists", conn.execute("SELECT COUNT(*) FROM artists").fetchone()[0])
        with stats_grid[1]:
            st.metric("Complete", conn.execute("SELECT COUNT(*) FROM artists WHERE collection_status='complete'").fetchone()[0])
        with stats_grid[2]:
            st.metric("Recordings", conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0])
        with stats_grid[3]:
            st.metric("Genres", conn.execute("SELECT COUNT(DISTINCT normalized_name) FROM tags WHERE tag_type='genre'").fetchone()[0])
        with stats_grid[4]:
            st.metric("Similarity edges", conn.execute("SELECT COUNT(*) FROM artist_similarities").fetchone()[0])

        clip_cov = get_clip_coverage(conn)
        st.subheader("Media Coverage")
        clip_grid = st.columns(4)
        with clip_grid[0]: st.metric("Total recordings", clip_cov["total"])
        with clip_grid[1]: st.metric("With preview clips", clip_cov["with_clips"])
        with clip_grid[2]: st.metric("With local file", clip_cov["with_local"])
        with clip_grid[3]: st.metric("In preview_misses", clip_cov["no_clip"])

        clip_chart_data = pd.DataFrame({
            "Status": ["Has clip (local)", "No clip (missed)", "No clip (not in misses)"],
            "Count": [clip_cov["with_local"], clip_cov["no_clip"],
                      clip_cov["total"] - clip_cov["with_clips"] - clip_cov["no_clip"]],
        })
        st.bar_chart(clip_chart_data.set_index("Status"), horizontal=True)

        st.subheader("Top Genres by Artist Count")
        genre_data = get_genre_coverage(conn)
        if genre_data:
            genre_df = pd.DataFrame(genre_data, columns=["genre", "complete", "partial", "stub", "total"])
            for col in ["complete", "partial", "stub", "total"]:
                genre_df[col] = genre_df[col].astype(int)
            genre_chart = genre_df.nlargest(20, "total").set_index("genre")
            st.bar_chart(genre_chart[["complete", "partial", "stub"]], horizontal=True)
            st.dataframe(genre_df.nlargest(30, "total"), width="stretch", hide_index=True)

        st.subheader("Planned Quota Gaps (genre_quotas_gaps.toml)")
        quotas = load_genre_quotas_gaps()
        if quotas:
            quota_df = pd.DataFrame(quotas)
            st.dataframe(quota_df, width="stretch", hide_index=True)
            st.caption(f"Planned expansion: **{sum(q['count'] for q in quotas)}** new artists across **{len(quotas)}** genre slots")
            chart_df = quota_df.sort_values("count", ascending=True)
            st.bar_chart(chart_df.set_index("name")["count"], horizontal=True)
        else:
            st.info("No quota slots found.")

        st.subheader("Fetch Provenance")
        prov_df = get_provider_counts(conn)
        if not prov_df.empty:
            st.dataframe(prov_df, width="stretch", hide_index=True)
            prov_chart = prov_df.set_index("provider")
            st.bar_chart(prov_chart[["live", "cached"]])

        st.subheader("Ingestion Jobs")
        jobs = conn.execute(
            "SELECT id, command, status, started_at, finished_at "
            "FROM ingestion_jobs ORDER BY id DESC LIMIT 20"
        ).fetchall()
        if jobs:
            st.dataframe(pd.DataFrame([dict(r) for r in jobs]), width="stretch", hide_index=True)

        st.subheader("Similarity Sources")
        sim_sources = conn.execute(
            "SELECT provider, COUNT(*) AS cnt FROM artist_similarities "
            "GROUP BY provider ORDER BY cnt DESC"
        ).fetchall()
        if sim_sources:
            st.bar_chart(pd.DataFrame([dict(r) for r in sim_sources]).set_index("provider")["cnt"], horizontal=True)

    # ===================================================================
    # TAB 3: Embedding Map
    # ===================================================================
    with tab_embeddings:
        st.header("Track Embedding Map (2D)")
        st.caption("Interactive projection of track embeddings.")

        conn = _get_conn()
        specs = conn.execute(
            "SELECT s.spec_hash, s.model_name, s.modality, s.dim, "
            "(SELECT COUNT(*) FROM embeddings e WHERE e.spec_hash = s.spec_hash AND e.entity_type = 'track') AS n_tracks "
            "FROM embedding_specs s ORDER BY n_tracks DESC, s.created_at DESC"
        ).fetchall()

        if not specs:
            st.info("No embedding specs found in the database.")
        else:
            spec_options = {s["spec_hash"]: f"{s['model_name']} ({s['modality']}, dim={s['dim']}, {s['n_tracks']} tracks)" for s in specs}
            selected_spec = st.selectbox("Embedding model", options=list(spec_options.keys()),
                                         format_func=lambda x: spec_options[x])

            if selected_spec:
                spec_info = next(s for s in specs if s["spec_hash"] == selected_spec)
                st.caption(f"Selected: {spec_info['model_name']} ({spec_info['modality']}, dim={spec_info['dim']})")

                use_umap = st.checkbox("Use UMAP (better quality, slower)", value=True)
                max_points = st.slider("Max points to show", min_value=500, max_value=10000, value=5000, step=500)

                with st.spinner("Computing projection..."):
                    # Fetch IDs
                    track_ids = conn.execute(
                        "SELECT entity_id FROM embeddings WHERE entity_type='track' AND spec_hash=?",
                        (selected_spec,)
                    ).fetchall()
                    ids = [int(r["entity_id"]) for r in track_ids]

                    if len(ids) > max_points:
                        rng = np.random.RandomState(42)
                        sample_ids = sorted(rng.choice(ids, max_points, replace=False))
                    else:
                        sample_ids = ids

                    if sample_ids:
                        ids_str = ",".join(str(i) for i in sample_ids)
                        query = f"""
                            SELECT e.entity_id AS rec_id, a.name AS artist, r.title, g.genre, e.vectors
                            FROM embeddings e
                            JOIN recording_artists ra ON ra.recording_id = CAST(e.entity_id AS INTEGER)
                            JOIN artists a ON a.id = ra.artist_id
                            JOIN recordings r ON r.id = CAST(e.entity_id AS INTEGER)
                            LEFT JOIN (
                                SELECT artist_id, genre, weight,
                                       ROW_NUMBER() OVER (PARTITION BY artist_id ORDER BY weight DESC) AS rn
                                FROM artist_genres
                            ) g ON g.artist_id = ra.artist_id AND g.rn = 1
                            WHERE e.entity_type = 'track' AND e.spec_hash = ?
                              AND CAST(e.entity_id AS INTEGER) IN ({ids_str})
                        """
                        rows = conn.execute(query, (selected_spec,)).fetchall()
                    else:
                        rows = []

                    items: List[Dict[str, Any]] = []
                    vectors_list: List[np.ndarray] = []
                    for r in rows:
                        vec = _blob_to_array(r["vectors"])
                        if vec.size == 0 or vec.shape[0] == 0:
                            continue
                        vec_mean = vec.mean(axis=0) if vec.ndim == 2 else vec.ravel()
                        items.append({"rec_id": int(r["rec_id"]), "artist": r["artist"],
                                       "title": r["title"], "genre": r["genre"] or "unknown"})
                        vectors_list.append(vec_mean)

                    if not items:
                        st.info("No track embeddings found for this model.")
                    else:
                        X = np.stack(vectors_list)
                        norms = np.linalg.norm(X, axis=1, keepdims=True)
                        norms[norms == 0] = 1
                        X = X / norms

                        @st.cache_data(show_spinner="Computing projection...")
                        def _project2d(X: np.ndarray, use_umap: bool) -> Tuple[np.ndarray, str]:
                            if use_umap:
                                try:
                                    import umap
                                    nn = max(2, min(30, len(X) - 1))
                                    return umap.UMAP(n_components=2, n_neighbors=nn, min_dist=0.1,
                                                     metric="cosine", random_state=42).fit_transform(X), "umap"
                                except Exception:
                                    pass
                            try:
                                from sklearn.manifold import TSNE
                                perp = max(2.0, min(30, len(X) - 3))
                                return TSNE(n_components=2, perplexity=perp, metric="cosine",
                                            random_state=42, init="pca").fit_transform(X), "tsne"
                            except Exception:
                                from sklearn.decomposition import PCA
                                return PCA(n_components=2, random_state=42).fit_transform(X), "pca"

                        X, method = _project2d(X, use_umap)

                        method_used = method
                        layer_data = []
                        for i, item in enumerate(items):
                            layer_data.append({"lng": float(X[i, 0]), "lat": float(X[i, 1]),
                                               "artist": item["artist"], "title": item["title"],
                                               "genre": item["genre"], "rec_id": item["rec_id"],
                                               "color": _genre_color(item["genre"])})

                        st.success(f"Projection: **{method_used.upper()}** with {len(layer_data)} points")

                        df_map = pd.DataFrame(layer_data).rename(columns={"lng": "x", "lat": "y"})
                        import altair as alt
                        top = df_map["genre"].value_counts().index[:15].tolist()
                        df_map["genre_group"] = df_map["genre"].where(df_map["genre"].isin(top), "other")
                        pick = alt.selection_point(name="pick", fields=["rec_id"], on="click")
                        chart = (
                            alt.Chart(df_map).mark_circle(size=40, opacity=0.75)
                            .encode(
                                x=alt.X("x:Q", axis=None, scale=alt.Scale(zero=False)),
                                y=alt.Y("y:Q", axis=None, scale=alt.Scale(zero=False)),
                                color=alt.Color("genre_group:N", title="Genre (top 15)"),
                                tooltip=["artist", "title", "genre"],
                            )
                            .add_params(pick)
                            .properties(height=600)
                            .interactive()
                        )
                        ev = st.altair_chart(chart, width="stretch", on_select="rerun")
                        picked = (ev.selection.get("pick") or []) if ev else []
                        if picked:
                            row = df_map[df_map["rec_id"] == picked[0]["rec_id"]].iloc[0]
                            st.session_state["map_selected"] = {
                                "rec_id": int(row["rec_id"]), "artist": row["artist"], "title": row["title"]}
                            st.caption(f"Selected: **{row['artist']}** - {row['title']} (play it in the Audio Playback tab)")

                        # Stats
                        c1, c2, c3 = st.columns(3)
                        with c1: st.metric("Points shown", len(df_map))
                        with c2: st.metric("Genres represented", df_map["genre"].nunique())
                        with c3: st.metric("Projection method", method_used)

    # ===================================================================
    # TAB 4: Audio Playback
    # ===================================================================
    with tab_audio:
        st.header("Audio Playback")
        st.caption("Play preview clips (30s) stored locally on disk.")
        conn = _get_conn()

        has_clips = conn.execute(
            "SELECT COUNT(*) FROM audio_clips WHERE local_path IS NOT NULL AND local_path != ''"
        ).fetchone()[0] > 0

        if not has_clips:
            st.info("No local audio clips found in this database.")
            st.stop()

        selection_mode = st.radio("How to select a track:",
                                  ["Browse by artist", "Browse by recording",
                                   "Click from embedding map"], horizontal=True)

        selected_recording_id: Optional[int] = None
        selected_artist_name = ""
        selected_title = ""
        selected_local_path = ""

        if selection_mode == "Click from embedding map":
            sel = st.session_state.get("map_selected")
            if isinstance(sel, dict):
                selected_recording_id = sel.get("rec_id")
                selected_artist_name = sel.get("artist", "")
                selected_title = sel.get("title", "")
            else:
                st.info("Go to the **Embedding Map** tab and click a point.")

        elif selection_mode == "Browse by artist":
            artists = conn.execute(
                "SELECT id, name FROM artists WHERE collection_status='complete' OR is_canonical=1 "
                "ORDER BY name LIMIT 200"
            ).fetchall()
            artist_names = {r["name"]: r["id"] for r in artists}
            selected_artist_name = st.selectbox("Artist", options=list(artist_names.keys()) + ["..."])

            if selected_artist_name != "...":
                al = conn.execute("""
                    SELECT r.id, r.title, r.duration_ms, ac.local_path, ac.preview_url
                    FROM recordings r
                    JOIN recording_artists ra ON ra.recording_id = r.id
                    LEFT JOIN audio_clips ac ON ac.track_id = r.id
                    WHERE ra.artist_id = ? AND ac.local_path IS NOT NULL AND ac.local_path != ''
                    ORDER BY r.title
                """, (artist_names[selected_artist_name],)).fetchall()

                if al:
                    tt = {f"{r['title']} (ID: {r['id']})": r for r in al}
                    sel_track = st.selectbox("Track", options=list(tt.keys()))
                    if sel_track:
                        rec = tt[sel_track]
                        selected_recording_id, selected_title, selected_local_path = rec["id"], rec["title"], rec["local_path"]

        elif selection_mode == "Browse by recording":
            total = conn.execute("""
                SELECT COUNT(DISTINCT r.id) FROM recordings r
                JOIN audio_clips ac ON ac.track_id = r.id
                WHERE ac.local_path IS NOT NULL AND ac.local_path != ''
            """).fetchone()[0]
            page_size = 50
            page = st.number_input("Page", min_value=1, value=1,
                                   max_value=max(1, (total + page_size - 1) // page_size))
            offset = (page - 1) * page_size
            recs = conn.execute("""
                SELECT r.id, r.title, r.duration_ms, ac.local_path, ac.preview_url, a.name AS artist
                FROM recordings r
                JOIN recording_artists ra ON ra.recording_id = r.id
                JOIN artists a ON a.id = ra.artist_id
                JOIN audio_clips ac ON ac.track_id = r.id
                WHERE ac.local_path IS NOT NULL AND ac.local_path != ''
                GROUP BY r.id
                ORDER BY a.name, r.title LIMIT ? OFFSET ?
            """, (page_size, offset)).fetchall()

            if recs:
                opts = [f"{r['artist']} - {r['title']}" for r in recs]
                sel_track = st.selectbox("Track", options=opts)
                if sel_track:
                    rec = recs[opts.index(sel_track)]
                    selected_recording_id, selected_artist_name, selected_title = rec["id"], rec["artist"], rec["title"]
                    selected_local_path = rec["local_path"]

        # Play the selected track
        if selected_local_path:
            norm_path = selected_local_path.replace("\\", "/")
            st.markdown(f"### Now Playing\n**{selected_title}** by **{selected_artist_name}**")

            if Path(norm_path).exists():
                st.audio(norm_path, format="audio/mpeg")
                row = conn.execute(
                    "SELECT preview_url FROM audio_clips WHERE track_id = ? AND preview_url IS NOT NULL",
                    (selected_recording_id,)
                ).fetchone()
                if row and row["preview_url"]:
                    st.markdown(f"[🔗 Deezer preview URL]({row['preview_url']})")
            else:
                st.warning(f"Audio file not found on disk: `{norm_path}`")
                row = conn.execute(
                    "SELECT preview_url FROM audio_clips WHERE track_id = ? AND preview_url IS NOT NULL",
                    (selected_recording_id,)
                ).fetchone()
                if row and row["preview_url"]:
                    st.markdown(f'[🔗 Play Deezer preview]({row["preview_url"]})')
                else:
                    st.error("No audio source available for this track.")

        elif selected_recording_id and selection_mode == "Click from embedding map":
            row = conn.execute("""
                SELECT r.id, r.title, ac.local_path, ac.preview_url, a.name AS artist
                FROM recordings r
                JOIN recording_artists ra ON ra.recording_id = r.id
                JOIN artists a ON a.id = ra.artist_id
                LEFT JOIN audio_clips ac ON ac.track_id = r.id
                WHERE r.id = ?
            """, (selected_recording_id,)).fetchone()
            if row:
                selected_artist_name, selected_title = row["artist"], row["title"]
                if row["local_path"]:
                    norm_path = row["local_path"].replace("\\", "/")
                    st.markdown(f"### Now Playing\n**{selected_title}** by **{selected_artist_name}**")
                    if Path(norm_path).exists():
                        st.audio(norm_path, format="audio/mpeg")
                    else:
                        st.warning(f"Audio file not found: `{norm_path}`")
                    if row["preview_url"]:
                        st.markdown(f"[🔗 Deezer preview URL]({row['preview_url']})")
                else:
                    st.info("This track has no available audio preview.")
            else:
                st.warning("Track not found in database.")

    # ---- Footer ----
    st.markdown("---")
    st.caption(f"Music Data Visualizer • `{DB_PATH}` • Concurrent-safe read (WAL + busy_timeout) • Cache: `{CACHE_DIR}`")


if __name__ == "__main__":
    _run_app()
