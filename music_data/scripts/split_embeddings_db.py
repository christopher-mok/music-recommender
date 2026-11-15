"""Move embedding tables out of the main DB into a sibling file.

    python scripts/split_embeddings_db.py data/music_metadata_100.db

Produces ``data/music_metadata_100.embeddings.db`` holding embedding_specs,
embeddings and artist_vectors, drops them from the main DB, and VACUUMs both,
so each file stays under GitHub's 100 MB limit. ``music_data.database.connect``
attaches the sibling automatically, so code keeps working unchanged.

Safe to re-run: tables already in the embeddings file are merged (INSERT OR
REPLACE) and then removed from main. Run it again after a pipeline run that
created embeddings in a fresh DB.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from music_data.database import EMBEDDING_TABLES, embeddings_db_path  # noqa: E402


def split(db_path: Path) -> None:
    emb_path = embeddings_db_path(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ATTACH DATABASE ? AS emb", (str(emb_path),))

    moved = 0
    with conn:
        for table in EMBEDDING_TABLES:
            row = conn.execute(
                "SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if row is None:
                continue
            exists = conn.execute(
                "SELECT 1 FROM emb.sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                create = row[0].replace(f"CREATE TABLE {table}", f"CREATE TABLE emb.{table}", 1)
                conn.execute(create)
                for (idx_sql,) in conn.execute(
                    "SELECT sql FROM main.sqlite_master WHERE type='index' "
                    "AND tbl_name=? AND sql IS NOT NULL", (table,)
                ).fetchall():
                    conn.execute(idx_sql.replace(" INDEX IF NOT EXISTS ", " INDEX IF NOT EXISTS emb.", 1)
                                 if " IF NOT EXISTS " in idx_sql
                                 else idx_sql.replace(" INDEX ", " INDEX emb.", 1))
            n = conn.execute(f"INSERT OR REPLACE INTO emb.{table} SELECT * FROM main.{table}").rowcount
            conn.execute(f"DROP TABLE main.{table}")
            moved += 1
            print(f"  {table}: {n} rows -> {emb_path.name}")

    if not moved:
        print("Nothing to move; embedding tables are already split.")
    conn.execute("DETACH DATABASE emb")
    conn.execute("VACUUM")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    e = sqlite3.connect(str(emb_path))
    e.execute("VACUUM")
    e.execute("PRAGMA journal_mode=WAL")
    e.close()
    for p in (db_path, emb_path):
        print(f"{p}: {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    split(Path(sys.argv[1]))
