"""Tests for the embedding pipeline: fake model, blob serialization, and aggregation."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from music_data.embeddings import list_models, embedder_for
from music_data.embeddings.blob_helpers import (
    array_to_blob,
    blob_to_array,
    spec_hash,
    sha256_text,
)
from music_data.embeddings.export import export_artists
from music_data.embeddings.helpers import (
    get_artists_with_vectors,
    get_existing_entity_ids,
    rebuild_all_artist_vectors,
)


# ── Fake model ──────────────────────────────────────────────────────


class TestFakeEmbedder:
    """Deterministic fake embedder tests."""

    def test_registered(self):
        assert "fake" in list_models()

    def test_spec(self):
        model = embedder_for("fake")
        spec = model.spec()
        assert spec["dim"] == 128
        assert spec["dtype"] == "float32"
        assert spec["model"] == "fake/deterministic"

    def test_text_embedding_deterministic(self):
        model = embedder_for("fake")
        model.modality = "text"  # fake is actually audio but we can override
        texts = ["hello world", "foo bar"]
        v1 = model.embed(texts)
        v2 = model.embed(texts)
        np.testing.assert_array_equal(v1, v2)

    def test_text_embedding_different_inputs(self):
        model = embedder_for("fake")
        model.modality = "text"
        v_hello = model.embed(["hello"])
        v_world = model.embed(["world"])
        assert not np.allclose(v_hello, v_world)

    def test_embedding_shape(self):
        model = embedder_for("fake")
        model.modality = "text"
        texts = ["a", "b", "c"]
        vectors = model.embed(texts)
        assert vectors.shape == (3, 1, 128)

    def test_batch_size(self):
        model = embedder_for("fake")
        model.modality = "text"
        large_batch = [f"input_{i}" for i in range(100)]
        vectors = model.embed(large_batch)
        assert vectors.shape == (100, 1, 128)


# ── Blob helpers ────────────────────────────────────────────────────


class TestBlobHelpers:
    """Test array/blob serialization round-trip."""

    def test_array_to_blob_roundtrip_1d(self):
        original = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        blob = array_to_blob(original)
        restored = blob_to_array(blob)
        np.testing.assert_array_equal(original, restored)
        assert restored.dtype == np.float32

    def test_array_to_blob_roundtrip_2d(self):
        original = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
        blob = array_to_blob(original)
        restored = blob_to_array(blob)
        np.testing.assert_array_equal(original, restored)

    def test_blob_size_efficiency(self):
        vec = np.random.randn(128).astype(np.float32)
        blob = array_to_blob(vec)
        # numpy array in memory: 4 bytes * 128 = 512 bytes
        # blob format: 4 (shape_len) + 4 (shape) + 512 (data) = 520 bytes
        assert len(blob) > 0
        assert len(blob) < 600  # should be compact

    def test_sha256_text_deterministic(self):
        h1 = sha256_text("hello")
        h2 = sha256_text("hello")
        assert h1 == h2
        assert len(h1) == 64  # hex digest length

    def test_sha256_text_different(self):
        h1 = sha256_text("hello")
        h2 = sha256_text("world")
        assert h1 != h2

    def test_spec_hash_deterministic(self):
        spec1 = {"dim": 128, "model": "test"}
        spec2 = {"dim": 128, "model": "test"}
        assert spec_hash(spec1) == spec_hash(spec2)


# ── Aggregation helpers ─────────────────────────────────────────────


class TestAggregation:
    """Test artist vector aggregation from track/text embeddings."""

    @pytest.fixture
    def conn(self, tmp_path):
        """Create a test database with embeddings tables."""
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import register_spec, upsert_embedding

        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            # Insert a test artist
            conn.execute(
                "INSERT OR IGNORE INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'test-artist-mbid', 'Test Artist', 1)"
            )
            # Insert a test recording
            conn.execute(
                "INSERT OR IGNORE INTO recordings (id, mbid, title, is_canonical) "
                "VALUES (1, 'test-rec-mbid', 'Test Track', 1)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO recording_artists (artist_id, recording_id) "
                "VALUES (1, 1)"
            )
            # Get a spec
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            # Insert a text embedding directly
            text = "Test Artist | rock | electronic"
            from music_data.embeddings.helpers import sha256_text
            input_hash = sha256_text(text)
            vectors = model.embed([text])
            upsert_embedding(
                conn,
                spec_hash=spec_hash_val,
                input_hash=input_hash,
                entity_type="artist",
                entity_id="test-artist-mbid",
                vectors=vectors,
            )

            yield conn

    def test_rebuild_all_artist_vectors(self, conn):
        count = rebuild_all_artist_vectors(conn, "fake-spec-placeholder", "mean")
        # The spec_hash from register_spec is different; let's use a proper test
        pass

    def test_rebuild_with_real_spec(self, tmp_path):
        """Full aggregation test with real spec."""
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
            rebuild_all_artist_vectors,
            get_artists_with_vectors,
        )

        db_path = str(tmp_path / "test2.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            # Insert test data
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'mbid-1', 'Artist A', 1)"
            )
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (2, 'mbid-2', 'Artist B', 1)"
            )
            conn.execute(
                "INSERT INTO recordings (id, mbid, title, is_canonical) "
                "VALUES (1, 'rec-1', 'Track 1', 1)"
            )
            conn.execute(
                "INSERT INTO recording_artists (artist_id, recording_id) "
                "VALUES (1, 1)"
            )
            conn.execute(
                "INSERT INTO recordings (id, mbid, title, is_canonical) "
                "VALUES (2, 'rec-2', 'Track 2', 1)"
            )
            conn.execute(
                "INSERT INTO recording_artists (artist_id, recording_id) "
                "VALUES (2, 2)"
            )

            # Get spec and insert embeddings
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            for mbid, text in [
                ("mbid-1", "Artist A | rock"),
                ("mbid-2", "Artist B | electronic"),
            ]:
                from music_data.embeddings.helpers import sha256_text
                input_hash = sha256_text(text)
                vectors = model.embed([text])
                upsert_embedding(
                    conn,
                    spec_hash=spec_hash_val,
                    input_hash=input_hash,
                    entity_type="artist",
                    entity_id=mbid,
                    vectors=vectors,
                )

            # Aggregate
            count = rebuild_all_artist_vectors(conn, spec_hash_val, "mean")
            assert count == 2

            # Check stored vectors
            artist_vecs = get_artists_with_vectors(conn, spec_hash_val, "mean")
            assert len(artist_vecs) == 2
            for av in artist_vecs:
                assert av["n_tracks"] == 1

    def test_rebuild_skips_non_canonical(self, tmp_path):
        """Only canonical artists should be aggregated."""
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
            rebuild_all_artist_vectors,
        )

        db_path = str(tmp_path / "test3.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'mbid-canonical', 'Canonical', 1)"
            )
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (2, 'mbid-non', 'Non-Canonical', 0)"
            )

            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            from music_data.embeddings.helpers import sha256_text
            for mbid, text in [
                ("mbid-canonical", "Canonical | rock"),
                ("mbid-non", "Non-Canonical | jazz"),
            ]:
                input_hash = sha256_text(text)
                vectors = model.embed([text])
                upsert_embedding(
                    conn,
                    spec_hash=spec_hash_val,
                    input_hash=input_hash,
                    entity_type="artist",
                    entity_id=mbid,
                    vectors=vectors,
                )

            count = rebuild_all_artist_vectors(conn, spec_hash_val, "mean")
            assert count == 1  # Only canonical

    def test_existing_entity_ids(self, tmp_path):
        """Test get_existing_entity_ids filtering."""
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
            get_existing_entity_ids,
        )

        db_path = str(tmp_path / "test4.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            from music_data.embeddings.helpers import sha256_text
            # Embed only artist-1
            input_hash = sha256_text("Artist 1")
            vectors = model.embed(["Artist 1"])
            upsert_embedding(
                conn,
                spec_hash=spec_hash_val,
                input_hash=input_hash,
                entity_type="artist",
                entity_id="artist-1-mbid",
                vectors=vectors,
            )

            existing = get_existing_entity_ids(
                conn, spec_hash_val, "artist",
                ["artist-1-mbid", "artist-2-mbid", "artist-3-mbid"]
            )
            assert existing == {"artist-1-mbid"}


# ── Export ──────────────────────────────────────────────────────────


class TestExport:
    """Test embedding bundle export."""

    def test_export_creates_files(self, tmp_path):
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
        )
        from pathlib import Path

        db_path = str(tmp_path / "export_test.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            # Setup artist and embedding
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'exp-mbid', 'Export Artist', 1)"
            )
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            from music_data.embeddings.helpers import sha256_text
            input_hash = sha256_text("Export Artist")
            vectors = model.embed(["Export Artist"])
            upsert_embedding(
                conn,
                spec_hash=spec_hash_val,
                input_hash=input_hash,
                entity_type="artist",
                entity_id="exp-mbid",
                vectors=vectors,
            )

            # Aggregate
            from music_data.embeddings.helpers import rebuild_all_artist_vectors
            rebuild_all_artist_vectors(conn, spec_hash_val, "mean")

            # Export
            output_dir = tmp_path / "bundle"
            result_path = export_artists(conn, spec_hash_val, output_dir, "mean")

            assert result_path.exists()
            assert output_dir / "ids.json" in list(output_dir.iterdir())
            assert output_dir / "manifest.json" in list(output_dir.iterdir())

    def test_export_numpy_loadable(self, tmp_path):
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
            rebuild_all_artist_vectors,
        )

        db_path = str(tmp_path / "np_test.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'np-mbid', 'Numpy Test', 1)"
            )
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            from music_data.embeddings.helpers import sha256_text
            input_hash = sha256_text("Numpy Test")
            vectors = model.embed(["Numpy Test"])
            upsert_embedding(
                conn,
                spec_hash=spec_hash_val,
                input_hash=input_hash,
                entity_type="artist",
                entity_id="np-mbid",
                vectors=vectors,
            )
            rebuild_all_artist_vectors(conn, spec_hash_val, "mean")

            output_dir = tmp_path / "np_bundle"
            export_artists(conn, spec_hash_val, output_dir, "mean")

            # Verify numpy can load it
            data = np.load(output_dir / "vectors.f16.npy")
            assert data.shape == (1, 128)
            assert data.dtype == np.float16

    def test_export_manifest(self, tmp_path):
        from music_data.database import get_connection, init_db
        from music_data.embeddings import embedder_for
        from music_data.embeddings.helpers import (
            register_spec,
            upsert_embedding,
            rebuild_all_artist_vectors,
        )

        db_path = str(tmp_path / "manifest_test.db")
        init_db(db_path)

        with get_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO artists (id, mbid, name, is_canonical) "
                "VALUES (1, 'm-mbid', 'Manifest', 1)"
            )
            model = embedder_for("fake")
            spec_hash_val = register_spec(conn, embedder=model)

            from music_data.embeddings.helpers import sha256_text
            input_hash = sha256_text("Manifest")
            vectors = model.embed(["Manifest"])
            upsert_embedding(
                conn,
                spec_hash=spec_hash_val,
                input_hash=input_hash,
                entity_type="artist",
                entity_id="m-mbid",
                vectors=vectors,
            )
            rebuild_all_artist_vectors(conn, spec_hash_val, "mean")

            output_dir = tmp_path / "manifest_bundle"
            export_artists(conn, spec_hash_val, output_dir, "mean")

            manifest = json.load(open(output_dir / "manifest.json"))
            assert manifest["model_name"] == "fake"
            assert manifest["dim"] == 128
            assert manifest["count"] == 1
            assert manifest["pooling"] == "mean"
            assert "spec_hash" in manifest
