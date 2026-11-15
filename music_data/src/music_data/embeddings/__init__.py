"""Swappable embedding models registry and protocol.

Each model is a file registered by name under ``REGISTRY``.  A model
implements the :py:class:`Embedder` protocol so that the pipeline never
depends on a concrete implementation.

Usage::

    from music_data.embeddings import REGISTRY, embedder_for

    model = embedder_for("fake")
    vectors = model.embed(["audio.mp3", "other.mp3"])
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    """Interface every model must satisfy."""

    name: str  # registry key, e.g. "embeddinggemma2-audio"
    modality: str  # "audio" | "text"

    def spec(self) -> dict:
        """Return everything that affects output vectors.

        Must include:
        - ``model``: repo / model identifier string
        - ``revision``: pinned commit/tag (string)
        - ``dim``: output dimension (int)
        - ``dtype``: numpy dtype string, e.g. "float32"
        - ``normalization``: "l2" or null

        Audio models should additionally include ``sample_rate``,
        ``mono`` (bool), ``window_seconds``, ``hop_seconds``.
        Text models may include ``template``, ``prompt``.
        """
        ...

    def embed(self, inputs: list) -> np.ndarray:
        """Embed a batch of inputs.

        Parameters
        ----------
        inputs : list[str]
            For audio: file paths to audio clips.
            For text: the raw text strings.

        Returns
        -------
        np.ndarray
            ``(n, window, dim)`` for audio (pooled per window) or
            ``(n, dim)`` for text.  Uses the spec's dtype.
        """
        ...


# ------------------------------------------------------------------
# Registry helpers
# ------------------------------------------------------------------
REGISTRY: dict[str, type[Embedder]] = {}


def register(name: str) -> type:
    """Decorator to register an Embedder subclass by *name*."""
    def wrapper(cls: type[Embedder]) -> type[Embedder]:
        REGISTRY[name] = cls
        return cls
    return wrapper


def embedder_for(name: str) -> Embedder:
    """Instantiate and return an embedder from *name*."""
    cls = REGISTRY.get(name)
    if cls is None:
        raise KeyError(
            f"Unknown model '{name}'.  Available: {sorted(REGISTRY.keys())}"
        )
    return cls()


def list_models() -> list[str]:
    """Return sorted model names."""
    return sorted(REGISTRY.keys())


# ── Auto-register all embedder modules ─────────────────────────────
# Importing these modules triggers their @register decorators.

from importlib import import_module as _im

for _m in ("fake", "text_embedder", "audio_embedder", "audio_models"):
    _im(f".{_m}", __name__)  # type: ignore
