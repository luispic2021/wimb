from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from wimb.deviation import build_bus_facts, is_still_approaching
from wimb.gtfs import GtfsStore
from wimb.models import RealtimeStopUpdate, ScheduledRun, StopStatus, TripUpdate, VehiclePosition


def _run(gtfs: GtfsStore, trip_id: str, now: datetime) -> ScheduledRun:
    return next(
        run
        for run in gtfs.scheduled_runs_at_stop("154", "B", 0, now)
        if run.stop_time.trip_id == trip_id and run.service_date == date(2026, 7, 13)
    )


def _update(trip_id: str, stop_id: str, sequence: int, delay: int) -> TripUpdate:
    return TripUpdate(
        trip_id,
        "154",
        "1204",
        (RealtimeStopUpdate(stop_id, sequence, None, delay),),
    )


def test_late_scheduled_past_bus_stays_visible_while_approaching(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)  # scheduled at B at 07:12
    position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-3", "A", 1, 480)], [position], now)

    assert len(facts) == 1
    assert facts[0].scheduled_departure < now
    assert facts[0].as_of_stop.name == "Novato"
    assert (facts[0].run_number, facts[0].run_total) == (3, 7)


def test_early_bus_stays_visible_while_approaching(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-4", now)  # scheduled at B at 07:42
    position = VehiclePosition("sb-4", "1204", "B", None, StopStatus.INCOMING_AT, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-4", "A", 1, -120)], [position], now)

    assert len(facts) == 1
    assert facts[0].scheduled_departure > now
    assert facts[0].deviation_seconds == -120


def test_bus_past_selected_stop_is_excluded(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    position = VehiclePosition("sb-3", "1204", "C", 3, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-3", "B", 2, 240)], [position], now)

    assert facts == []


def test_stopped_at_selected_stop_uses_current_stop_evidence(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.STOPPED_AT, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-3", "B", 2, 45)], [position], now)

    assert facts[0].as_of_stop == gtfs_store.stops["B"]


def test_future_prediction_is_not_presented_as_historical_evidence(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-4", now)
    position = VehiclePosition("sb-4", "1204", "B", 2, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-4", "B", 2, -180)], [position], now)

    assert facts == []


def test_missing_or_incomplete_realtime_evidence_is_withheld(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    unknown_position = VehiclePosition("sb-3", "1204", None, None, StopStatus.UNKNOWN, now)

    assert build_bus_facts(gtfs_store, [run], [], [unknown_position], now) == []
    assert (
        build_bus_facts(
            gtfs_store,
            [run],
            [_update("sb-3", "A", 1, 240)],
            [unknown_position],
            now,
        )
        == []
    )


def test_missing_progress_uses_only_bounded_schedule_fallback(gtfs_store: GtfsStore) -> None:
    scheduled = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", scheduled)
    position = VehiclePosition("sb-3", "1204", None, None, StopStatus.UNKNOWN, scheduled)

    assert is_still_approaching(gtfs_store, run, position, scheduled - timedelta(minutes=30))
    assert is_still_approaching(gtfs_store, run, position, scheduled + timedelta(minutes=60))
    assert not is_still_approaching(
        gtfs_store, run, position, scheduled - timedelta(minutes=30, seconds=1)
    )
    assert not is_still_approaching(
        gtfs_store, run, position, scheduled + timedelta(minutes=60, seconds=1)
    )
