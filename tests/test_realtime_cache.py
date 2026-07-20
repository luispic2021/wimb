from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

from google.transit import gtfs_realtime_pb2

from wimb.realtime_cache import CachedTransitClient


class _Client:
    def __init__(self, gate: Event | None = None, started: Event | None = None) -> None:
        self.trip_calls = 0
        self.vehicle_calls = 0
        self.gate = gate
        self.started = started

    def fetch_operators(self) -> list[dict[str, Any]]:
        return []

    def fetch_gtfs(self, _operator_id: str) -> bytes:
        return b""

    def fetch_trip_updates(self, _operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        self.trip_calls += 1
        if self.started is not None:
            self.started.set()
        if self.gate is not None:
            assert self.gate.wait(timeout=2)
        return _feed(self.trip_calls)

    def fetch_vehicle_positions(self, _operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        self.vehicle_calls += 1
        return _feed(self.vehicle_calls)


def _feed(timestamp: int) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = timestamp
    return feed


def test_reuses_trip_updates_and_vehicle_positions_within_ttl() -> None:
    now = [100.0]
    raw = _Client()
    cached = CachedTransitClient(raw, ttl_seconds=60, monotonic_clock=lambda: now[0])

    trip_feed = cached.fetch_trip_updates("GG")
    vehicle_feed = cached.fetch_vehicle_positions("GG")
    cached.fetch_trip_updates("GG")

    assert trip_feed.header.timestamp == 1
    assert vehicle_feed.header.timestamp == 1
    assert (raw.trip_calls, raw.vehicle_calls) == (1, 1)

    now[0] += 60
    cached.fetch_vehicle_positions("GG")

    assert (raw.trip_calls, raw.vehicle_calls) == (2, 2)


def test_concurrent_callers_share_one_realtime_refresh() -> None:
    gate = Event()
    started = Event()
    raw = _Client(gate, started)
    cached = CachedTransitClient(raw)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(cached.fetch_trip_updates, "GG")
        assert started.wait(timeout=2)
        second = executor.submit(cached.fetch_vehicle_positions, "GG")
        gate.set()
        trip_feed = first.result(timeout=2)
        vehicle_feed = second.result(timeout=2)

    assert trip_feed.header.timestamp == 1
    assert vehicle_feed.header.timestamp == 1
    assert (raw.trip_calls, raw.vehicle_calls) == (1, 1)
