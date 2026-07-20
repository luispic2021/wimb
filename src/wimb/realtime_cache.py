"""One-process realtime feed cache with refresh deduplication."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Condition
from typing import Any

from google.transit import gtfs_realtime_pb2  # type: ignore[import-untyped]

from .client import TransitDataClient

REALTIME_CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class _RealtimeEntry:
    operator_id: str
    trip_updates: gtfs_realtime_pb2.FeedMessage
    vehicle_positions: gtfs_realtime_pb2.FeedMessage
    stored_at: float


class CachedTransitClient:
    """Decorate a transit client and cache both realtime feeds as one snapshot.

    A condition serializes refreshes. Callers arriving during a refresh wait for
    that refresh and reuse its result instead of issuing duplicate 511 requests.
    This cache is process-local and is therefore intended for one Uvicorn worker.
    """

    def __init__(
        self,
        client: TransitDataClient,
        ttl_seconds: float = REALTIME_CACHE_TTL_SECONDS,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Realtime cache TTL must be positive.")
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._clock = monotonic_clock
        self._condition = Condition()
        self._entry: _RealtimeEntry | None = None
        self._refreshing = False

    def fetch_operators(self) -> list[dict[str, Any]]:
        return self._client.fetch_operators()

    def fetch_gtfs(self, operator_id: str) -> bytes:
        return self._client.fetch_gtfs(operator_id)

    def fetch_trip_updates(self, operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self._feeds(operator_id).trip_updates

    def fetch_vehicle_positions(self, operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self._feeds(operator_id).vehicle_positions

    def _feeds(self, operator_id: str) -> _RealtimeEntry:
        with self._condition:
            while True:
                now = self._clock()
                if (
                    self._entry is not None
                    and self._entry.operator_id == operator_id
                    and now - self._entry.stored_at < self._ttl_seconds
                ):
                    return self._entry
                if not self._refreshing:
                    self._refreshing = True
                    break
                self._condition.wait()

        try:
            trip_feed = self._client.fetch_trip_updates(operator_id)
            vehicle_feed = self._client.fetch_vehicle_positions(operator_id)
        except BaseException:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()
            raise

        with self._condition:
            self._entry = _RealtimeEntry(
                operator_id,
                trip_feed,
                vehicle_feed,
                self._clock(),
            )
            self._refreshing = False
            self._condition.notify_all()
            return self._entry
