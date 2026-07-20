from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from wimb.deviation import build_bus_facts, build_bus_listing, is_still_approaching
from wimb.gtfs import GtfsStore
from wimb.models import (
    ProgressEvidence,
    RealtimeStopUpdate,
    ScheduledRun,
    StopStatus,
    TrackingStatus,
    TripUpdate,
    VehiclePosition,
)


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


def test_stopped_at_with_conflicting_update_stop_is_withheld(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.STOPPED_AT, now)
    conflicting_update = TripUpdate(
        "sb-3",
        "154",
        "1204",
        (RealtimeStopUpdate("A", 2, None, 45),),
    )

    facts = build_bus_facts(gtfs_store, [run], [conflicting_update], [position], now)

    assert facts == []


def test_stopped_at_with_conflicting_update_sequence_is_withheld(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 12, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.STOPPED_AT, now)
    conflicting_update = TripUpdate(
        "sb-3",
        "154",
        "1204",
        (RealtimeStopUpdate("B", 1, None, 45),),
    )

    facts = build_bus_facts(gtfs_store, [run], [conflicting_update], [position], now)

    assert facts == []


def test_future_prediction_is_not_presented_as_historical_evidence(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-4", now)
    position = VehiclePosition("sb-4", "1204", "B", 2, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(gtfs_store, [run], [_update("sb-4", "B", 2, -180)], [position], now)

    assert facts == []


def test_evidence_stop_matches_older_available_update_when_intermediate_is_missing(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = next(
        run
        for run in gtfs_store.scheduled_runs_at_stop("154", "C", 0, now)
        if run.stop_time.trip_id == "sb-3" and run.service_date == date(2026, 7, 13)
    )
    position = VehiclePosition("sb-3", "1204", "C", 3, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(
        gtfs_store,
        [run],
        [_update("sb-3", "A", 1, 120)],
        [position],
        now,
    )

    assert facts[0].as_of_stop == gtfs_store.stops["A"]
    assert facts[0].as_of_stop_sequence == 1
    assert facts[0].deviation_seconds == 120


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


def test_listing_fills_count_with_honest_not_departed_timetable_run(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    runs = gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)
    run = _run(gtfs_store, "sb-3", now)
    position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.IN_TRANSIT_TO, now)

    buses, exhausted = build_bus_listing(
        gtfs_store,
        runs,
        [_update("sb-3", "A", 1, 480)],
        [position],
        now,
        count=2,
    )

    assert [bus.run_number for bus in buses] == [run.run_number, 4]
    assert buses[0].tracking_status is TrackingStatus.TRACKED
    assert buses[1].tracking_status is TrackingStatus.NOT_DEPARTED
    assert buses[1].vehicle_id is None
    assert not exhausted


def test_listing_marks_started_approaching_run_without_evidence_unavailable(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 35, tzinfo=UTC)
    runs = gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)
    position = VehiclePosition("sb-4", "1204", None, None, StopStatus.IN_TRANSIT_TO, now)

    buses, _exhausted = build_bus_listing(gtfs_store, runs, [], [position], now, count=1)

    assert buses[0].run_number == 4
    assert buses[0].vehicle_id == "1204"
    assert buses[0].tracking_status is TrackingStatus.UNAVAILABLE


def test_listing_marks_preassigned_vehicle_as_not_departed_before_scheduled_start(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    runs = gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)
    position = VehiclePosition("sb-4", "1204", None, None, StopStatus.UNKNOWN, now)

    buses, _exhausted = build_bus_listing(gtfs_store, runs, [], [position], now, count=1)

    assert buses[0].run_number == 4
    assert buses[0].tracking_status is TrackingStatus.NOT_DEPARTED
    assert buses[0].vehicle_id is None


def test_listing_includes_after_midnight_run_before_service_date_ends(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 23, 0, tzinfo=UTC)
    runs = gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)

    buses, exhausted = build_bus_listing(gtfs_store, runs, [], [], now, count=2)

    assert len(buses) == 1
    assert buses[0].run_number == 7
    assert buses[0].scheduled_departure == datetime(2026, 7, 14, 1, 12, tzinfo=UTC)
    assert buses[0].tracking_status is TrackingStatus.NOT_DEPARTED
    assert exhausted


def test_listing_keeps_cached_upstream_run_when_vehicle_position_disappears(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    runs = gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)
    run = _run(gtfs_store, "sb-3", now)
    prior = ProgressEvidence(
        trip_id="sb-3",
        service_date=run.service_date,
        stop_sequence=1,
        stop=gtfs_store.stops["A"],
        delay_seconds=480,
        observed_at=now - timedelta(minutes=1),
    )

    buses, _exhausted = build_bus_listing(
        gtfs_store,
        runs,
        [],
        [],
        now,
        count=1,
        prior_progress={(run.service_date, "sb-3"): prior},
    )

    assert buses[0].run_number == 3
    assert buses[0].tracking_status is TrackingStatus.UNAVAILABLE
    assert buses[0].vehicle_id is None


def test_checkpoint_evidence_prevents_as_of_stop_regression(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = next(
        run
        for run in gtfs_store.scheduled_runs_at_stop("154", "C", 0, now)
        if run.stop_time.trip_id == "sb-3" and run.service_date == date(2026, 7, 13)
    )
    prior = ProgressEvidence(
        trip_id="sb-3",
        service_date=run.service_date,
        stop_sequence=2,
        stop=gtfs_store.stops["B"],
        delay_seconds=480,
        observed_at=now - timedelta(minutes=1),
    )
    regressed_position = VehiclePosition("sb-3", "1204", "B", 2, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(
        gtfs_store,
        [run],
        [_update("sb-3", "A", 1, 120)],
        [regressed_position],
        now,
        {(run.service_date, "sb-3"): prior},
    )

    assert facts[0].as_of_stop == gtfs_store.stops["B"]
    assert facts[0].as_of_stop_sequence == 2
    assert facts[0].deviation_seconds == 480


def test_checkpoint_at_selected_stop_excludes_departed_bus_when_feed_loses_progress(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    prior = ProgressEvidence(
        trip_id="sb-3",
        service_date=run.service_date,
        stop_sequence=2,
        stop=gtfs_store.stops["B"],
        delay_seconds=0,
        observed_at=now - timedelta(minutes=1),
    )
    unknown_progress = VehiclePosition("sb-3", "1204", None, None, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(
        gtfs_store,
        [run],
        [_update("sb-3", "B", 2, 0)],
        [unknown_progress],
        now,
        {(run.service_date, "sb-3"): prior},
    )

    assert facts == []


def test_checkpoint_at_selected_stop_overrides_regressed_vehicle_sequence(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = _run(gtfs_store, "sb-3", now)
    prior = ProgressEvidence(
        trip_id="sb-3",
        service_date=run.service_date,
        stop_sequence=2,
        stop=gtfs_store.stops["B"],
        delay_seconds=0,
        observed_at=now - timedelta(minutes=1),
    )
    regressed_position = VehiclePosition("sb-3", "1204", "A", 1, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(
        gtfs_store,
        [run],
        [_update("sb-3", "A", 1, 0)],
        [regressed_position],
        now,
        {(run.service_date, "sb-3"): prior},
    )

    assert facts == []


def test_checkpoint_supplies_evidence_when_trip_update_temporarily_disappears(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 13, 7, 20, tzinfo=UTC)
    run = next(
        run
        for run in gtfs_store.scheduled_runs_at_stop("154", "C", 0, now)
        if run.stop_time.trip_id == "sb-3" and run.service_date == date(2026, 7, 13)
    )
    prior = ProgressEvidence(
        trip_id="sb-3",
        service_date=run.service_date,
        stop_sequence=2,
        stop=gtfs_store.stops["B"],
        delay_seconds=180,
        observed_at=now - timedelta(minutes=1),
    )
    position = VehiclePosition("sb-3", "1204", "C", 3, StopStatus.IN_TRANSIT_TO, now)

    facts = build_bus_facts(
        gtfs_store,
        [run],
        [],
        [position],
        now,
        {(run.service_date, "sb-3"): prior},
    )

    assert facts[0].as_of_stop == gtfs_store.stops["B"]
    assert facts[0].deviation_seconds == 180
