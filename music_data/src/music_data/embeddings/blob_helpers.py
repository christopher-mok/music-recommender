"""Blob serialization helpers for numpy arrays in SQLite.

Vectors are stored as raw little-endian float32 bytes (no numpy header)
for compactness and cross-platform compatibility.
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import numpy as np


# ------------------------------------------------------------------
# Serialization
# ------------------------------------------------------------------
def array_to_blob(arr: np.ndarray) -> bytes:
    """Serialize a numpy array to raw little-endian bytes.

    Layout: ``<int32 shape_len><int32 *shape><float32 *data>``
    """
    buf = io.BytesIO()
    # Write shape (int32 length + int32 dims)
    shape = arr.shape
    buf.write(np.int32(len(shape)).tobytes())
    buf.write(np.array(shape, dtype=np.int32).tobytes())
    # Write data (ensure float32, little-endian)
    data = arr.astype(np.float32, copy=False).ravel()
    buf.write(data.tobytes(order="C"))
    return buf.getvalue()


def blob_to_array(blob: bytes, *, expected_dim: int | None = None) -> np.ndarray:
    """Deserialize a blob back to a float32 numpy array."""
    buf = io.BytesIO(blob)
    shape_len = np.frombuffer(buf.read(4), dtype=np.int32)[0]
    dims = np.frombuffer(buf.read(4 * shape_len), dtype=np.int32)
    data = np.frombuffer(buf.read(), dtype=np.float32)
    result = data.reshape(dims.tolist())
    if expected_dim is not None and result.shape[-1] != expected_dim:
        raise ValueError(
            f"Blob dimension {result.shape[-1]} != expected {expected_dim}"
        )
    return result


# ------------------------------------------------------------------
# Hashing utilities
# ------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a UTF-8 encoded string."""
    return sha256_bytes(text.encode("utf-8"))


def sha256_pcm(pcm_data: bytes) -> str:
    """Hex SHA-256 of decoded PCM audio bytes."""
    return sha256_bytes(pcm_data)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON for hashing specs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def spec_hash(spec: dict) -> str:
    """Compute the spec_hash for an embedding spec dict."""
    return sha256_bytes(canonical_json(spec).encode("utf-8"))
