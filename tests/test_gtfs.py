from __future__ import annotations

from datetime import UTC, date, datetime

from wimb.gtfs import GtfsStore, _parse_gtfs_time


def _run(gtfs: GtfsStore, trip_id: str, stop_id: str, now: datetime):
    return next(
        run
        for run in gtfs.scheduled_runs_at_stop("154", stop_id, None, now)
        if run.stop_time.trip_id == trip_id and run.service_date == date(2026, 7, 13)
    )


def test_numbers_seven_southbound_runs_by_first_trip_departure(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 0, tzinfo=UTC)

    run = _run(gtfs_store, "sb-6", "B", now)

    assert (run.run_number, run.run_total) == (6, 7)
    assert run.direction_label == "Southbound to San Francisco"


def test_run_number_is_stable_across_selected_stops(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 7, 0, tzinfo=UTC)

    at_origin = _run(gtfs_store, "sb-4", "A", now)
    downstream = _run(gtfs_store, "sb-4", "C", now)

    assert (at_origin.run_number, at_origin.run_total) == (4, 7)
    assert (downstream.run_number, downstream.run_total) == (4, 7)


def test_numbers_northbound_independently_and_derives_direction(gtfs_store: GtfsStore) -> None:
    now = datetime(2026, 7, 13, 17, 0, tzinfo=UTC)

    run = _run(gtfs_store, "nb-5", "B", now)

    assert (run.run_number, run.run_total) == (5, 7)
    assert run.direction_label == "Northbound to Novato"


def test_calendar_date_removal_and_addition_override_calendar(gtfs_store: GtfsStore) -> None:
    exception_date = date(2026, 7, 14)
    now = datetime(2026, 7, 14, 9, 0, tzinfo=UTC)

    runs = [
        run
        for run in gtfs_store.scheduled_runs_at_stop("154", "B", 0, now)
        if run.service_date == exception_date
    ]

    assert not gtfs_store.service_runs("WEEKDAY", exception_date)
    assert gtfs_store.service_runs("SPECIAL", exception_date)
    assert [(run.stop_time.trip_id, run.run_number, run.run_total) for run in runs] == [
        ("special-sb", 1, 1)
    ]


def test_after_midnight_time_keeps_previous_service_date_and_run_number(
    gtfs_store: GtfsStore,
) -> None:
    now = datetime(2026, 7, 14, 1, 0, tzinfo=UTC)

    run = _run(gtfs_store, "sb-7", "B", now)

    assert _parse_gtfs_time("25:10:00").total_seconds() == 90_600
    assert run.scheduled_departure == datetime(2026, 7, 14, 1, 12, tzinfo=UTC)
    assert run.service_date == date(2026, 7, 13)
    assert (run.run_number, run.run_total) == (7, 7)


def test_uses_previous_stop_for_in_transit_vehicle(gtfs_store: GtfsStore) -> None:
    assert gtfs_store.previous_stop("sb-3", 3) == gtfs_store.stops["B"]
