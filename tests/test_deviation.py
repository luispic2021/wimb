from __future__ import annotations

from datetime import UTC, datetime

from wimb.deviation import build_bus_facts
from wimb.gtfs import GtfsStore
from wimb.models import RealtimeStopUpdate, StopStatus, TripUpdate, VehiclePosition


def test_delay_is_labeled_as_of_last_passed_stop_for_in_transit_bus(gtfs_store: GtfsStore) -> None:
    scheduled = gtfs_store.stop_times["trip-154"][1]
    departure = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    update = TripUpdate(
        "trip-154",
        "154",
        "1204",
        (RealtimeStopUpdate("B", 2, None, 240),),
    )
    position = VehiclePosition("trip-154", "1204", None, 3, StopStatus.IN_TRANSIT_TO, departure)

    facts = build_bus_facts(gtfs_store, [(scheduled, departure)], [update], [position])

    assert len(facts) == 1
    assert facts[0].deviation_seconds == 240
    assert facts[0].as_of_stop.name == "Manzanita Park & Ride"
    assert facts[0].scheduled_departure == departure


def test_missing_current_stop_delay_is_not_presented_as_a_forecast(gtfs_store: GtfsStore) -> None:
    scheduled = gtfs_store.stop_times["trip-154"][1]
    departure = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    update = TripUpdate("trip-154", "154", "1204", (RealtimeStopUpdate("C", 3, None, 240),))
    position = VehiclePosition("trip-154", "1204", None, 3, StopStatus.IN_TRANSIT_TO, departure)

    assert build_bus_facts(gtfs_store, [(scheduled, departure)], [update], [position]) == []
