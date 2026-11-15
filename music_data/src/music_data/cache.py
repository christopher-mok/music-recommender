"""Simple on-disk JSON response cache.

The cache stores raw provider responses so they can be inspected and replayed
without re-calling the API. Files are keyed by a hash of the request and are
safe to keep in version control if the provider's terms allow it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def make_cache_key(provider: str, method: str, url: str, body: Any = None) -> str:
    raw = json.dumps(
        {"provider": provider, "method": method.upper(), "url": url, "body": body},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HttpCache:
    """Filesystem-backed cache for JSON API responses."""

    def __init__(
        self,
        directory: str | Path,
        *,
        enabled: bool = True,
        read: bool = True,
        write: bool = True,
        ttl_seconds: int = 0,
    ) -> None:
        self.directory = Path(directory).expanduser()
        self.enabled = enabled
        self.read = read
        self.write = write
        self.ttl_seconds = ttl_seconds
        if self.enabled and (self.read or self.write):
            self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not (self.enabled and self.read):
            return None
        path = self.path_for(key)
        if not path.exists():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable cache entry %s: %s", path, exc)
            return None

        if self.ttl_seconds > 0:
            fetched_at = document.get("fetched_at", "")
            try:
                age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(fetched_at)
                ).total_seconds()
            except (TypeError, ValueError):
                age = self.ttl_seconds + 1
            if age > self.ttl_seconds:
                logger.debug("Cache entry expired: %s", path)
                return None
        return document

    def put(
        self,
        key: str,
        *,
        url: str,
        status_code: int,
        payload: Any,
        fetched_at: str | None = None,
    ) -> Path | None:
        if not (self.enabled and self.write):
            return None
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "key": key,
            "url": url,
            "status_code": status_code,
            "fetched_at": fetched_at or _utc_now(),
            "payload": payload,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return path
