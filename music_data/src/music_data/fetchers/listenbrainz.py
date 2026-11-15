"""ListenBrainz public API and Dataset Hoster clients."""

from __future__ import annotations

from typing import Any

from music_data.fetchers.base import BaseFetcher, CollectionError
from music_data.models import ArtistSeed, SimilarArtist


class ListenBrainzFetcher(BaseFetcher):
    """Fetch public popularity and similar-artist data from ListenBrainz."""

    provider = "listenbrainz"

    def __init__(
        self,
        *,
        api_url: str = "https://api.listenbrainz.org",
        labs_url: str = "https://labs.api.listenbrainz.org",
        algorithm: str,
        similar_limit: int = 50,
        rate_limit: float = 2.0,
        user_token: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            base_url=api_url,
            user_agent=kwargs.pop("user_agent", "music_data_pipeline/0.2"),
            rate_limit=rate_limit,
            **kwargs,
        )
        self.labs_url = labs_url.rstrip("/")
        self.algorithm = algorithm
        self.similar_limit = similar_limit
        if user_token:
            self.client.headers["Authorization"] = f"Token {user_token}"

    def popular_artists(
        self, *, count: int = 100, offset: int = 0, range_: str = "all_time"
    ) -> list[dict[str, Any]]:
        payload = self.get(
            "1/stats/sitewide/artists",
            {"count": count, "offset": offset, "range": range_},
        )
        result = payload.get("payload", {}).get("artists", []) if isinstance(payload, dict) else []
        return result if isinstance(result, list) else []

    def popular_artist_seeds(self, *, count: int = 100, range_: str = "this_year") -> list[ArtistSeed]:
        """Sitewide top artists as seeds, paging past entries without an MBID."""
        seeds: list[ArtistSeed] = []
        seen: set[str] = set()
        offset = 0
        page = min(max(count, 1), 100)
        while len(seeds) < count:
            rows = self.popular_artists(count=page, offset=offset, range_=range_)
            if not rows:
                break
            offset += len(rows)
            for rank, row in enumerate(rows, offset - len(rows) + 1):
                mbid = row.get("artist_mbid")
                name = row.get("artist_name")
                # Multi-artist credits have no single MBID; skip rather than guess.
                if not mbid or not name or mbid in seen:
                    continue
                seen.add(mbid)
                seeds.append(ArtistSeed(
                    name=name,
                    mbid=mbid,
                    reason=f"ListenBrainz sitewide top artist #{rank} ({range_}, "
                           f"{row.get('listen_count', '?')} listens)",
                ))
                if len(seeds) >= count:
                    break
        return seeds

    def artist_popularity(self, artist_mbids: list[str]) -> dict[str, dict[str, int]]:
        """Total listen and listener counts per artist MBID (batched POST)."""
        result: dict[str, dict[str, int]] = {}
        for start in range(0, len(artist_mbids), 100):
            batch = artist_mbids[start:start + 100]
            payload = self.post("1/popularity/artist", {"artist_mbids": batch})
            for row in payload if isinstance(payload, list) else []:
                mbid = row.get("artist_mbid")
                if mbid:
                    result[mbid] = {
                        "listens": int(row.get("total_listen_count") or 0),
                        "listeners": int(row.get("total_user_count") or 0),
                    }
        return result

    def popular_recordings(self, artist_mbid: str) -> list[dict[str, Any]]:
        payload = self.get(f"1/popularity/top-recordings-for-artist/{artist_mbid}")
        if not isinstance(payload, list):
            raise CollectionError("Unexpected ListenBrainz popular-recordings response")
        return payload

    def similar_artists(self, artist_mbid: str, *, limit: int | None = None) -> list[SimilarArtist]:
        body = [{"artist_mbids": [artist_mbid], "algorithm": self.algorithm}]
        # The dataset hoster takes its own base URL. Keep the shared fetcher
        # behavior (timeouts, retries, cache, events) and temporarily set URL.
        original = self.base_url
        try:
            self.base_url = self.labs_url
            payload = self.post("similar-artists/json", body)
        finally:
            self.base_url = original
        if not isinstance(payload, list) or not payload:
            raise CollectionError("Unexpected ListenBrainz similar-artists response")

        # Dataset Hoster's JSON route currently returns a flat list of result
        # objects. Some wrapped clients expose one nested list per request.
        if isinstance(payload[0], dict):
            records = payload
        elif isinstance(payload[0], list):
            records = payload[0]
        else:
            raise CollectionError("Unexpected ListenBrainz similar-artists response")

        result: list[SimilarArtist] = []
        max_items = limit if limit is not None else self.similar_limit
        for item in records[:max_items]:
            mbid = item.get("artist_mbid")
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            score = item.get("score")
            result.append(
                SimilarArtist(
                    mbid=str(mbid) if mbid else None,
                    name=name,
                    score=float(score) if score is not None else None,
                    raw_score=str(score) if score is not None else None,
                    description=item.get("comment"),
                    artist_type=item.get("type"),
                )
            )
        return result
