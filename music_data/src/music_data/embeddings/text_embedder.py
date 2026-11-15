"""Lightweight text embedder.

Uses *sentence-transformers* (transformers + sentencepiece) when available.
Falls back to the ``fake`` model when heavy deps are not installed.

The model is configurable via the ``EMBEDDING_MODEL`` environment variable
(defaulting to ``sentence-transformers/all-MiniLM-L6-v2``) and pinned by
revision/commit for reproducibility.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from music_data.embeddings import Embedder, register

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_REVISION = "main"


@register("allminilm")
class MiniLmTextEmbedder(Embedder):
    """Sentence-transformers based text embedder."""

    name = "allminilm"
    modality = "text"

    def __init__(
        self,
        model_name: str | None = None,
        revision: str = DEFAULT_REVISION,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name or os.environ.get(
            "EMBEDDING_MODEL", DEFAULT_MODEL
        )
        self._revision = revision
        self._device = device
        self._model: SentenceTransformer | None = None
        # Cached spec to avoid re-importing
        self._cached_spec: dict | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer as ST
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for 'allminilm'. "
                    "Install with: pip install sentence-transformers"
                )
            logger.info("Loading text model: %s (revision %s)", self._model_name, self._revision)
            self._model = ST(
                self._model_name,
                revision=self._revision,
                device=self._device,
            )
        return self._model

    def spec(self) -> dict:
        if self._cached_spec is not None:
            return self._cached_spec
        model = self.model
        self._cached_spec = {
            "model": self._model_name,
            "revision": self._revision,
            "dim": int(model.get_sentence_embedding_dimension()),
            "dtype": "float32",
            "normalization": "l2",
            "template": None,
        }
        return self._cached_spec

    def embed(self, inputs: list) -> np.ndarray:
        if not inputs:
            return np.zeros((0, self.spec()["dim"]), dtype=np.float32)
        model = self.model
        # sentence-transformers returns normalized float32 by default
        vectors = model.encode(
            inputs,
            show_progress_bar=False,
            normalize_embeddings=True,
            batch_size=32,
        )
        return np.asarray(vectors, dtype=np.float32)


# ------------------------------------------------------------------
# Fallback: when sentence-transformers is not installed, use 'fake'
# ------------------------------------------------------------------
@register("text-fallback")
class TextFallbackEmbedder(Embedder):
    """Minimal text embedder — delegates to the fake model.

    This is a convenience wrapper so that ``--model text-fallback`` works
    without installing sentence-transformers, and the resulting vectors
    are deterministic for testing.
    """

    name = "text-fallback"
    modality = "text"

    def __init__(self) -> None:
        from music_data.embeddings import REGISTRY

        cls = REGISTRY.get("fake")
        if cls is None:
            raise RuntimeError("fake embedder not registered — internal error")
        self._delegate = cls()

    def spec(self) -> dict:
        base = self._delegate.spec()
        base["model"] = "fake/text-fallback"
        base["modality"] = "text"
        return base

    def embed(self, inputs: list) -> np.ndarray:
        return self._delegate.embed(inputs)
