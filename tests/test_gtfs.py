from __future__ import annotations

from datetime import UTC, datetime

from wimb.gtfs import GtfsStore


def test_parses_route_stop_and_upcoming_departure(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 0, tzinfo=UTC)  # Monday

    upcoming = gtfs_store.upcoming_at_stop("154", "B", 0, now)

    assert gtfs_store.routes["154"] == "Novato - San Francisco"
    assert gtfs_store.stops["B"].name == "Manzanita Park & Ride"
    assert (upcoming[0][0].trip_id, upcoming[0][1].hour, upcoming[0][1].minute) == (
        "trip-154",
        7,
        12,
    )


def test_uses_previous_stop_for_in_transit_vehicle(gtfs_store: GtfsStore) -> None:
    assert gtfs_store.previous_stop("trip-154", 3) == gtfs_store.stops["B"]


def test_preserves_gtfs_times_after_midnight() -> None:
    from wimb.gtfs import _parse_gtfs_time

    assert _parse_gtfs_time("25:10:00").total_seconds() == 90_600
