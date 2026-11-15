"""Real audio embedders: MuQ-MuLan and EmbeddingGemma 2.

Both produce per-window vectors ``(n_windows, dim)`` per clip so pooling can
change later without re-running the model. Everything that changes the output
(repo, pinned revision, sample rate, window/hop, dtype, dim) is in ``spec()``.

Heavy deps (torch, librosa, muq, sentence-transformers) are imported lazily.
"""

from __future__ import annotations

import logging
import os

import numpy as np

from music_data.embeddings import register

logger = logging.getLogger(__name__)


def _device() -> str:
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _resolve_revision(repo: str, revision: str | None) -> str:
    """Pin to a commit sha so the spec hash is reproducible."""
    if revision and len(revision) == 40:
        return revision
    try:
        from huggingface_hub import HfApi
        return HfApi().model_info(repo, revision=revision or "main").sha
    except Exception as exc:  # offline: fall back to the ref name
        logger.warning("Could not resolve %s revision (%s)", repo, exc)
        return revision or "main"


def load_mono(path: str, sr: int) -> np.ndarray:
    import librosa
    wav, _ = librosa.load(path, sr=sr, mono=True)
    return wav.astype(np.float32)


def windows(wav: np.ndarray, sr: int, win_s: float, hop_s: float) -> list[np.ndarray]:
    """Fixed windows; the tail shorter than half a window is dropped."""
    win, hop = int(win_s * sr), int(hop_s * sr)
    if len(wav) <= win:
        return [wav]
    out = [wav[i:i + win] for i in range(0, len(wav) - win + 1, hop)]
    rest = len(wav) - (len(out) - 1) * hop - win
    if rest >= win // 2:
        out.append(wav[-win:])
    return out


class _AudioBase:
    modality = "audio"
    repo: str
    sample_rate: int
    window_seconds: float
    hop_seconds: float
    dim: int

    def __init__(self) -> None:
        self._model = None
        self._revision = _resolve_revision(
            self.repo, os.environ.get(f"{self.name.upper().replace('-', '_')}_REVISION"))

    def spec(self) -> dict:
        return {
            "model": self.repo,
            "revision": self._revision,
            "dim": self.dim,
            "dtype": self.dtype,
            "normalization": "l2",
            "sample_rate": self.sample_rate,
            "mono": True,
            "window_seconds": self.window_seconds,
            "hop_seconds": self.hop_seconds,
            "resampler": "librosa.load(soxr_hq)",
        }

    def embed(self, inputs: list) -> list[np.ndarray]:
        out = []
        for path in inputs:
            segs = windows(load_mono(path, self.sample_rate), self.sample_rate,
                           self.window_seconds, self.hop_seconds)
            vecs = np.asarray(self._embed_windows(segs), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-12)
            out.append(vecs)
        return out


@register("muq-mulan")
class MuQMuLanEmbedder(_AudioBase):
    """OpenMuQ/MuQ-MuLan-large — music-text joint space, 512-d, 24 kHz."""

    name = "muq-mulan"
    repo = "OpenMuQ/MuQ-MuLan-large"
    sample_rate = 24000
    window_seconds = 10.0
    hop_seconds = 10.0
    dim = 512
    dtype = "float32"

    def _load(self):
        if self._model is None:
            from muq import MuQMuLan
            # PyTorchModelHubMixin.from_pretrained accepts revision= via **kwargs.
            self._model = MuQMuLan.from_pretrained(self.repo, revision=self._revision)
            self._model = self._model.to(_device()).eval()
        return self._model

    def _embed_windows(self, segs):
        import torch
        model = self._load()
        batch = torch.from_numpy(np.stack(_pad(segs))).to(_device())
        with torch.no_grad():
            return model(wavs=batch).float().cpu().numpy()


@register("embeddinggemma2-audio")
class EmbeddingGemma2AudioEmbedder(_AudioBase):
    """google/embeddinggemma-2 audio encoder — shared 768-d multimodal space, 16 kHz."""

    name = "embeddinggemma2-audio"
    repo = "google/embeddinggemma-2"
    sample_rate = 16000
    window_seconds = 10.0
    hop_seconds = 10.0
    dim = 768
    dtype = "bfloat16"  # model card: float16 can produce NaNs

    def _load(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self.repo, revision=self._revision, device=_device(),
                config_kwargs={"vision_config": None},
                model_kwargs={"torch_dtype": torch.bfloat16},
            )
        return self._model

    def _embed_windows(self, segs):
        model = self._load()
        items = [{"audio": {"array": s, "sampling_rate": self.sample_rate}} for s in segs]
        return model.encode(items, convert_to_numpy=True, normalize_embeddings=True)


def _pad(segs: list[np.ndarray]) -> list[np.ndarray]:
    n = max(len(s) for s in segs)
    return [np.pad(s, (0, n - len(s))) for s in segs]
