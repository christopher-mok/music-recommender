"""Base HTTP fetcher with rate limiting, retries, and response caching."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Mapping

import httpx

from music_data.cache import HttpCache, make_cache_key

logger = logging.getLogger(__name__)


class CollectionError(Exception):
    """Raised when a collection operation fails."""


class PermanentCollectionError(CollectionError):
    """A request that should not be retried (4xx, invalid input)."""


@dataclass
class FetchEvent:
    provider: str
    method: str
    url: str
    status_code: int | None
    from_cache: bool
    cache_path: str | None
    response_hash: str | None
    fetched_at: str


class RateLimiter:
    """Simple minimum-interval limiter.

    A single interval is easier to reason about than a sliding window and is
    stricter for the MusicBrainz 1 req/sec requirement.
    """

    def __init__(self, max_requests_per_second: float = 1.0) -> None:
        self.max_rps = max(0.0, float(max_requests_per_second))
        self.interval = 1.0 / self.max_rps if self.max_rps > 0 else 0.0
        self._last_request = 0.0

    def wait(self) -> None:
        if self.interval <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_request
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_request = time.monotonic()


def parse_retry_after(value: str | None, default: float = 5.0) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return default


class BaseFetcher:
    """Shared HTTP behavior for provider clients."""

    provider: str = "unknown"

    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        timeout: float = 30.0,
        max_retries: int = 5,
        retry_delay: float = 1.0,
        rate_limit: float = 1.0,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        cache: HttpCache | None = None,
        use_cache: bool = True,
        on_fetch: Callable[[FetchEvent], None] | None = None,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.rate_limiter = RateLimiter(rate_limit)
        self.cache = cache
        self.use_cache = use_cache
        self.on_fetch = on_fetch

        if client is not None:
            self.client = client
        else:
            self.client = httpx.Client(transport=transport)
        self.client.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": user_agent,
            }
        )

    # ------------------------------------------------------------------
    def close(self) -> None:
        self.client.close()

    def _url(self, endpoint: str, params: Mapping[str, Any] | None) -> str:
        endpoint = endpoint.lstrip("/")
        url = f"{self.base_url}/{endpoint}" if endpoint else self.base_url
        if params:
            # Sort for deterministic cache keys and reproducible tests.
            pairs = []
            for key in sorted(params, key=str):
                value = params[key]
                if value is None:
                    continue
                if isinstance(value, (list, tuple)):
                    pairs.extend((str(key), str(item)) for item in value)
                else:
                    pairs.append((str(key), str(value)))
            url = str(httpx.URL(url, params=pairs))
        return url

    def _emit(
        self,
        *,
        method: str,
        url: str,
        status_code: int | None,
        from_cache: bool,
        cache_path: str | None,
        payload: Any,
    ) -> None:
        if not self.on_fetch:
            return
        try:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
        except (TypeError, ValueError):
            digest = None
        self.on_fetch(
            FetchEvent(
                provider=self.provider,
                method=method.upper(),
                url=url,
                status_code=status_code,
                from_cache=from_cache,
                cache_path=cache_path,
                response_hash=digest,
                fetched_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            )
        )

    # ------------------------------------------------------------------
    def request_json(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        use_cache: bool | None = None,
    ) -> Any:
        """Perform a JSON request, using the cache and retrying transient errors."""
        method = method.upper()
        url = self._url(endpoint, params)
        allow_cache = self.use_cache if use_cache is None else use_cache
        key = make_cache_key(self.provider, method, url, json_body)

        if allow_cache and self.cache:
            document = self.cache.get(key)
            if document is not None:
                logger.debug("Cache hit: %s %s", method, url)
                self._emit(
                    method=method,
                    url=url,
                    status_code=int(document.get("status_code", 200)),
                    from_cache=True,
                    cache_path=str(self.cache.path_for(key)),
                    payload=document.get("payload"),
                )
                return document.get("payload")

        last_error: Exception | None = None
        attempts = self.max_retries + 1

        for attempt in range(attempts):
            self.rate_limiter.wait()
            try:
                logger.debug("Request %s %s (attempt %s/%s)", method, url, attempt + 1, attempts)
                response = self.client.request(
                    method,
                    url,
                    json=json_body,
                    timeout=self.timeout,
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("Timeout for %s %s", method, url)
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning("Request error for %s %s: %s", method, url, exc)
            else:
                if response.status_code >= 400:
                    self._emit(
                        method=method,
                        url=url,
                        status_code=response.status_code,
                        from_cache=False,
                        cache_path=None,
                        payload={"error": self._error_message(response)},
                    )
                if response.status_code == 429:
                    wait = max(1.0, parse_retry_after(response.headers.get("Retry-After")))
                    logger.warning("Rate limited (429); sleeping %.1fs", wait)
                    last_error = CollectionError(f"HTTP 429 from {url}")
                    time.sleep(wait)
                    continue
                if 400 <= response.status_code < 500:
                    message = self._error_message(response)
                    raise PermanentCollectionError(
                        f"HTTP {response.status_code} from {url}: {message}"
                    )
                if response.status_code >= 500:
                    backoff = self.retry_delay * (2**attempt)
                    # MusicBrainz has returned ``Retry-After: 0`` on 503s;
                    # still apply exponential backoff rather than hammering it.
                    wait = max(
                        backoff,
                        parse_retry_after(response.headers.get("Retry-After"), backoff),
                    )
                    last_error = CollectionError(
                        f"HTTP {response.status_code} from {url}: {self._error_message(response)}"
                    )
                    logger.warning(
                        "Server error %s for %s; retrying in %.1fs",
                        response.status_code,
                        url,
                        wait,
                    )
                    if attempt < attempts - 1:
                        time.sleep(wait)
                    continue

                try:
                    payload = response.json()
                except ValueError as exc:
                    raise PermanentCollectionError(
                        f"Invalid JSON from {url}: {exc}"
                    ) from exc

                cache_path = None
                if allow_cache and self.cache:
                    path = self.cache.put(
                        key,
                        url=url,
                        status_code=response.status_code,
                        payload=payload,
                    )
                    cache_path = str(path) if path else None
                self._emit(
                    method=method,
                    url=url,
                    status_code=response.status_code,
                    from_cache=False,
                    cache_path=cache_path,
                    payload=payload,
                )
                return payload

            if attempt < attempts - 1:
                wait = self.retry_delay * (2**attempt)
                logger.debug("Retrying in %.1fs", wait)
                time.sleep(wait)

        raise CollectionError(f"Request failed after {attempts} attempts: {last_error}")

    def get(self, endpoint: str, params: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request_json("GET", endpoint, params=params, **kwargs)

    def post(self, endpoint: str, json_body: Any, **kwargs: Any) -> Any:
        return self.request_json("POST", endpoint, json_body=json_body, **kwargs)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:300]
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error)
            if error:
                return str(error)
        return str(payload)[:300]
