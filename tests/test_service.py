from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2

from wimb import service as service_module
from wimb.gtfs import GtfsStore
from wimb.service import WimbService


class _RealtimeClient:
    def __init__(self, timestamp: datetime) -> None:
        self.feed = gtfs_realtime_pb2.FeedMessage()
        self.feed.header.gtfs_realtime_version = "2.0"
        self.feed.header.timestamp = int(timestamp.timestamp())

    def fetch_trip_updates(self, _operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self.feed

    def fetch_vehicle_positions(self, _operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self.feed


def test_live_snapshot_time_is_captured_after_realtime_requests(
    gtfs_store: GtfsStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_time = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    completed_time = datetime(2026, 7, 13, 7, 20, 5, tzinfo=UTC)
    times = iter((request_time, completed_time))
    client = _RealtimeClient(completed_time)
    service = WimbService(client, tmp_path, stale_after_seconds=90, clock=lambda: next(times))  # type: ignore[arg-type]
    monkeypatch.setattr(WimbService, "_operator_id", lambda self, now: "GG")
    monkeypatch.setattr(
        service_module.GtfsStore,
        "cached",
        classmethod(lambda cls, client, cache_dir, operator_id, now: gtfs_store),
    )

    snapshot = service.snapshot("B", 0, 2)

    assert snapshot.fetched_at == completed_time
