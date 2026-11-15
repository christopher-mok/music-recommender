"""Download 30-second preview clips from the Deezer API.

Deezer's preview API is free and requires no authentication key.

Usage::

    from music_data.fetchers.previews import DeezerPreviewFetcher

    fetcher = DeezerPreviewFetcher()
    clips = fetcher.find_clips(conn, tracks)  # tracks from DB
    # clips: list of dicts with audio_sha256, provider_track_id, path
"""

from __future__ import annotations

import hashlib
import logging
import struct
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEEZER_SEARCH_URL = "https://api.deezer.com/2.0/search"
CACHE_DIR = Path(".cache/music_data/audio")


class DeezerPreviewFetcher:
    """Fetch 30s preview clips from Deezer."""

    def __init__(
        self,
        rate_limit: float = 2.0,  # requests per second
        timeout: float = 15.0,
    ) -> None:
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._last_request = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "music_data_pipeline/0.2"},
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        import time
        wait = (1.0 / self._rate_limit) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        return self._client.get(url, **kwargs)

    def find_clips(
        self,
        tracks: list[dict[str, Any]],
        *,
        max_duration_diff: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Search for and download preview clips for a list of tracks.

        Parameters
        ----------
        tracks : list[dict]
            Each dict: ``{"artist": str, "track": str, "duration": int|None,
            "track_id": int, "mbid_track": str|None}``
        max_duration_diff : float
            Maximum accepted duration mismatch (seconds).

        Returns
        -------
        list[dict]
            Each dict has: ``track_id, audio_sha256, mbid_track,
            provider, provider_track_id, preview_url, duration_s, path``
        """
        results: list[dict[str, Any]] = []
        misses: list[str] = []

        for track in tracks:
            artist = track.get("artist", "").strip().lower()
            track_name = track.get("track", "").strip().lower()
            duration_s = (track.get("duration") or 0) / 1000.0 if track.get("duration") else None

            clip = self._search_and_download(
                artist=artist,
                track=track_name,
                duration_s=duration_s,
                max_duration_diff=max_duration_diff,
                track_id=track["track_id"],
                mbid_track=track.get("mbid_track"),
            )
            if clip:
                results.append(clip)
            else:
                misses.append(f"{artist} — {track_name}")

        if misses:
            logger.info("Preview misses (%d): %s", len(misses), misses[:10])
        logger.info("Found %d/%d preview clips", len(results), len(tracks))
        return results

    def _search_and_download(
        self,
        *,
        artist: str,
        track: str,
        duration_s: float | None,
        max_duration_diff: float,
        track_id: int,
        mbid_track: str | None,
    ) -> dict[str, Any] | None:
        """Search Deezer for the best matching track and download its preview."""
        # Deezer's artist:"..." field filter returns nothing as of 2026-11; use a plain
        # query and validate artist/title/duration ourselves below.
        # Pass 1: strict (title + duration). Pass 2 (only if pass 1 fails): same
        # artist + core title (feat./remaster/live suffixes stripped), any duration,
        # because MusicBrainz often lists a different edit than Deezer.
        best = None
        for query in dict.fromkeys([f"{_q(artist)} {_q(track)}",
                                    f"{_q(artist)} {_q(_core_title(track))}"]):
            items = self._search(query)
            best = _pick(items, artist, track, duration_s, max_duration_diff) or                 _pick_relaxed(items, artist, track, duration_s)
            if best is not None:
                break

        if best is None:
            logger.debug("No matching preview for %s — %s", artist, track)
            return None

        return self._download_clip(
            preview_url=best["preview"],
            track_id=track_id,
            mbid_track=mbid_track,
            duration_s=float(best.get("duration") or 0),
            provider_track_id=str(best["id"]),
        )

    def _search(self, query: str) -> list[dict[str, Any]]:
        logger.debug("Searching Deezer: %s", query)
        try:
            resp = self._get(DEEZER_SEARCH_URL, params={"q": query, "limit": 10})
            resp.raise_for_status()
            return resp.json().get("data", []) or []
        except httpx.HTTPError as exc:
            logger.warning("Deezer search failed for '%s': %s", query, exc)
            return []

    def _download_clip(
        self,
        *,
        preview_url: str,
        track_id: int,
        mbid_track: str | None,
        duration_s: float,
        provider_track_id: str,
    ) -> dict[str, Any] | None:
        """Download a preview MP3 and compute its PCM hash."""
        try:
            resp = self._get(preview_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Failed to download preview %s: %s", preview_url, exc)
            return None

        file_bytes = resp.content
        # Save to cache directory
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Content address = hash of decoded PCM, so re-encoded copies match.
        sha = pcm_sha256(file_bytes)
        filename = f"{sha.replace(':', '_')}.mp3"
        path = CACHE_DIR / filename

        if path.exists():
            logger.debug("Preview already cached: %s", filename)
        else:
            path.write_bytes(file_bytes)

        # Compute duration from file if possible
        if duration_s <= 0:
            duration_s = _estimate_duration_mp3(file_bytes)

        return {
            "track_id": track_id,
            "audio_sha256": sha,
            "mbid_track": mbid_track,
            "provider": "deezer",
            "provider_track_id": provider_track_id,
            "preview_url": preview_url,
            "duration_s": _estimate_duration_mp3(file_bytes),
            "track_duration_s": duration_s,
            "path": str(path.resolve()),
        }


def _estimate_duration_mp3(data: bytes) -> float:
    """Rough duration estimate from MP3 header (seconds).

    MP3 frames are ~1152 samples at 44100 Hz → ~26.14 ms per frame.
    We can estimate from total file size and average bitrate.
    """
    if len(data) < 4:
        return 0.0
    # Skip ID3v2 header if present
    offset = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        # Read ID3v2 length (bytes 6-9, synchsafe integer)
        header_len = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
                     ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        offset = 10 + header_len

    # Skip sync word (0xFF 0xE*)
    while offset < len(data) - 1:
        if data[offset] == 0xFF and (data[offset + 1] & 0xE0) == 0xE0:
            # Found MPEG sync
            try:
                version_bit = (data[offset + 1] >> 3) & 0x03
                layer = (data[offset + 1] >> 1) & 0x03
                bitrate_idx = (data[offset + 2] >> 4) & 0x0F
                sample_rate_idx = (data[offset + 2] >> 2) & 0x03

                # Simple lookup (MPEG1 Layer III, common case)
                if version_bit == 0x03 and layer == 0x01:
                    bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
                    sample_rates = [44100, 48000, 32000]
                    if bitrate_idx < len(bitrates) and sample_rate_idx < len(sample_rates):
                        bitrate = bitrates[bitrate_idx] * 1000
                        sr = sample_rates[sample_rate_idx]
                        frame_size = 144 * bitrate // sr
                        if frame_size > 0:
                            # Approximate: remaining bytes / frame_size * frame_duration
                            remaining = len(data) - offset
                            frames = remaining // frame_size
                            duration = frames * (1152.0 / sr)
                            return round(duration, 1)
            except (IndexError, ZeroDivisionError):
                pass
            break
        offset += 1

    # Fallback: assume 128kbps (typical preview bitrate)
    estimated = (len(data) * 8) / (128000)
    return round(estimated, 1)


def _q(text: str) -> str:
    return text.replace('"', " ")


def _norm(text: str) -> str:
    import re
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def pcm_sha256(file_bytes: bytes) -> str:
    """SHA-256 of decoded mono float32 PCM at native sample rate.

    Falls back to the file-byte hash (prefixed ``file:``) if decoding fails.
    """
    import os
    import tempfile
    try:
        import librosa
        import numpy as np
        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(file_bytes)
            pcm, sr = librosa.load(tmp, sr=None, mono=True)
        finally:
            os.unlink(tmp)
        h = hashlib.sha256(np.int32(sr).tobytes())
        h.update(np.ascontiguousarray(pcm, dtype=np.float32).tobytes())
        return h.hexdigest()
    except Exception as exc:
        logger.warning("PCM decode failed (%s); hashing file bytes", exc)
        return "file:" + hashlib.sha256(file_bytes).hexdigest()


def _titles_match(item: dict, track: str, *, core: bool = False) -> bool:
    f = (lambda t: _norm(_core_title(t))) if core else _norm
    want = f(track)
    if not want:  # all-non-Latin titles normalise to "" -- compare raw instead
        want_raw = track.strip().lower()
        return any(want_raw and (item.get(k) or "").strip().lower() == want_raw
                   for k in ("title", "title_short"))
    return any(f(item.get(k) or "") == want for k in ("title", "title_short"))


def _same_artist(item: dict, artist: str) -> bool:
    a = _norm(artist)
    got = item.get("artist", {}).get("name", "")
    return _norm(got) == a if a else got.strip().lower() == artist.strip().lower()


def _pick(items, artist, track, duration_s, max_diff):
    """Strict: exact title, duration within max_diff (collabs need <=2s)."""
    best, best_diff = None, float("inf")
    for item in items:
        if not item.get("preview") or not _titles_match(item, track):
            continue
        diff = abs(float(item.get("duration") or 0) - duration_s) if duration_s else 0.0
        if duration_s and diff > max_diff:
            continue
        if not _same_artist(item, artist) and not (duration_s and diff <= 2.0):
            continue
        if diff < best_diff:
            best, best_diff = item, diff
    return best


def _pick_relaxed(items, artist, track, duration_s):
    """Same artist + core title, any duration; closest duration wins."""
    cands = [i for i in items if i.get("preview") and _same_artist(i, artist)
             and _titles_match(i, track, core=True)]
    if not cands:
        return None
    return min(cands, key=lambda i: abs(float(i.get("duration") or 0) - (duration_s or 0)))


_SUFFIX_WORDS = (
    r"feat\.?|ft\.?|featuring|with|remaster(?:ed)?|live|radio edit|edit|"
    r"version|mono|stereo|single|album|explicit|clean|bonus|deluxe|mix"
)
_SUFFIX_RES = None


def _core_title(title: str) -> str:
    """Strip '(feat. X)', '[Remastered 2011]', '- Live', '- Radio Edit' etc."""
    import re
    global _SUFFIX_RES
    if _SUFFIX_RES is None:
        w = _SUFFIX_WORDS
        _SUFFIX_RES = (
            re.compile(r"\s*[(\[][^)\]]*\b(?:" + w + r")\b[^)\]]*[)\]]", re.I),
            re.compile(r"\s+-\s+.*\b(?:" + w + r")\b.*$", re.I),
        )
    t = title
    for rx in _SUFFIX_RES:
        t = rx.sub("", t)
    return t.strip() or title
