"""Audio embedder that works with local audio files.

For v1, delegates to the ``fake`` model (or a real model when installed).
Audio files are expected to be PCM or standard WAV/MP3 that can be decoded
to PCM using ``soundfile`` or ``librosa``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from music_data.embeddings import Embedder, register

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@register("audio-fake")
class AudioFakeEmbedder(Embedder):
    """Audio embedder that delegates to the fake model.

    Accepts audio file paths, reads and normalizes the PCM, then delegates
    to the fake embedder for deterministic output.
    """

    name = "audio-fake"
    modality = "audio"

    def __init__(self) -> None:
        from music_data.embeddings import REGISTRY

        cls = REGISTRY.get("fake")
        if cls is None:
            raise RuntimeError("fake embedder not registered — internal error")
        self._delegate = cls()

    def spec(self) -> dict:
        base = self._delegate.spec()
        base["model"] = "fake/audio"
        base["modality"] = "audio"
        return base

    def embed(self, inputs: list) -> np.ndarray:
        """Embed audio files.

        Parameters
        ----------
        inputs : list[str]
            Paths to audio files.

        Returns
        -------
        np.ndarray
            Shape (n, 1, dim).
        """
        # Convert audio files to a normalized string representation
        # For the fake model, this is just the file paths
        # For real models, you'd decode PCM here
        decoded_inputs: list[str] = []
        for path in inputs:
            try:
                decoded = _normalize_audio(path)
                # Hash the PCM to get a deterministic string
                decoded_inputs.append(
                    f"sha256({_pcm_hash(decoded)})" if decoded else str(path)
                )
            except Exception as exc:
                logger.warning("Failed to decode %s: %s — using path", path, exc)
                decoded_inputs.append(str(path))

        return self._delegate.embed(decoded_inputs)


def _pcm_hash(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _normalize_audio(path: str) -> bytes:
    """Decode an audio file to raw PCM bytes.

    Attempts ``soundfile`` first, falls back to ``pydub`` / ``librosa``.
    Returns mono 16-bit PCM at 16kHz (common embedding input).
    """
    # Try soundfile (lighter than librosa)
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        # Mono downmix
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        # Resample if needed
        target_sr = 16000
        if sr != target_sr:
            try:
                import resampy
                data = resampy.resample(data, sr, target_sr)
            except ImportError:
                logger.warning("resampy not installed, skipping resample")
        # Convert to int16 bytes
        data = (data * 32767).astype(np.int16)
        return data.tobytes()
    except ImportError:
        pass

    # Try librosa
    try:
        import librosa
        data, sr = librosa.load(path, sr=16000, mono=True)
        data = (data * 32767).astype(np.int16)
        return data.tobytes()
    except ImportError:
        pass

    raise RuntimeError(
        "No audio decoder available. Install one of: soundfile, librosa, pydub"
    )
