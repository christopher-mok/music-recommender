"""Deterministic *fake* embedder for tests.

Produces reproducible vectors from the SHA-256 of each input so that:
- Tests don't need torch, librosa, or network access.
- Changing the fake spec (e.g. dimension) produces *different* vectors,
  triggering a full re-embed (same as a real model upgrade).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import numpy as np

from music_data.embeddings import Embedder, register

if TYPE_CHECKING:
    pass


@register("fake")
class FakeEmbedder(Embedder):
    """Deterministic fake embedder — always returns the same vectors per input."""

    name = "fake"
    modality: str = "audio"

    # Default spec values — can be overridden via **kwargs on __init__
    _default_spec: dict = {
        "model": "fake/deterministic",
        "revision": "v0.1.0",
        "dim": 128,
        "dtype": "float32",
        "normalization": None,
        "sample_rate": 44100,
        "mono": True,
        "window_seconds": 1.0,
        "hop_seconds": 0.5,
    }

    def __init__(self, **overrides: object) -> None:
        self._spec = {**self._default_spec, **{k: v for k, v in overrides.items() if v is not None}}

    def spec(self) -> dict:
        return dict(self._spec)

    def embed(self, inputs: list) -> np.ndarray:
        dim = self._spec["dim"]
        n = len(inputs)
        dtype = self._spec["dtype"]
        result: np.ndarray = np.zeros((n, 1, dim), dtype=dtype)

        for i, inp in enumerate(inputs):
            # Deterministic: hash the input → fill vector
            h = hashlib.sha256(str(inp).encode("utf-8")).digest()
            # Use repeated hash bytes to fill the vector deterministically
            repeat = max(1, dim // len(h) + 1)
            seed = np.frombuffer(h * repeat, dtype=np.uint8)[:dim]
            # Map bytes [0, 255] → floats in a reasonable range
            vec = (seed.astype(np.float32) - 127.5) / 127.5
            result[i, 0] = vec[:dim]

        return result

    @property
    def dim(self) -> int:
        return self._spec["dim"]
