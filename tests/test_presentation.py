from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from wimb.models import BusFact, RouteSnapshot, Stop, TrackingStatus
from wimb.presentation import render_snapshot


def _snapshot(
    deviation: int | None,
    observed_at: datetime | None,
    *,
    scheduled_offset: timedelta = timedelta(minutes=12),
    tracking_status: TrackingStatus = TrackingStatus.TRACKED,
    no_additional_buses: bool = False,
) -> RouteSnapshot:
    fetched_at = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
    bus = BusFact(
        trip_id="sb-6",
        service_date=date(2026, 7, 13),
        run_number=6,
        run_total=7,
        direction_label="Southbound to San Francisco",
        vehicle_id="1204" if tracking_status is TrackingStatus.TRACKED else None,
        scheduled_departure=fetched_at + scheduled_offset,
        deviation_seconds=deviation,
        as_of_stop=Stop("A", "Lucas Valley Road")
        if tracking_status is TrackingStatus.TRACKED
        else None,
        as_of_stop_sequence=20 if tracking_status is TrackingStatus.TRACKED else None,
        observed_at=observed_at,
        tracking_status=tracking_status,
    )
    return RouteSnapshot(
        "154",
        "Southbound to San Francisco",
        Stop("B", "Manzanita Park & Ride"),
        (bus,),
        fetched_at,
        no_additional_buses,
    )


def test_human_readable_rendering_uses_arrival_estimate_evidence_and_freshness() -> None:
    observed_at = datetime(2026, 7, 13, 15, 0, tzinfo=UTC) - timedelta(seconds=24)

    rendered = render_snapshot(_snapshot(-300, observed_at), color=False)

    assert "Route 154" in rendered
    assert "Direction: Southbound to San Francisco" in rendered
    assert "Stop: Manzanita Park & Ride" in rendered
    assert "Bus 6 of 7 · scheduled 3:12 PM · Vehicle 1204" in rendered
    assert "ETA: 3:07 PM" in rendered
    assert "Arrives in: 7 minutes and 00 seconds" in rendered
    assert "5 minutes and 00 seconds ahead as of Lucas Valley Road" in rendered
    assert "updated 24 sec ago" in rendered


def test_arrival_countdown_keeps_second_granularity_without_rounding() -> None:
    assert "Arrives in: 59 seconds" in render_snapshot(
        _snapshot(59, None, scheduled_offset=timedelta()), color=False
    )
    assert "Arrives in: 1 minute and 00 seconds" in render_snapshot(
        _snapshot(60, None, scheduled_offset=timedelta()), color=False
    )
    assert "Arrives in: 1 minute and 05 seconds" in render_snapshot(
        _snapshot(65, None, scheduled_offset=timedelta()), color=False
    )


def test_overdue_arrival_estimate_is_not_rendered_as_negative_countdown() -> None:
    rendered = render_snapshot(_snapshot(-65, None, scheduled_offset=timedelta()), color=False)

    assert "Arrival estimate overdue by: 1 minute and 05 seconds" in rendered


def test_missing_observation_timestamp_is_reported_honestly() -> None:
    rendered = render_snapshot(_snapshot(0, None), color=False)

    assert "updated time unavailable" in rendered


def test_timetable_only_states_and_exhaustion_are_explicit() -> None:
    rendered = render_snapshot(
        _snapshot(
            None,
            None,
            tracking_status=TrackingStatus.NOT_DEPARTED,
            no_additional_buses=True,
        ),
        color=False,
    )

    assert "Vehicle" not in rendered
    assert "Timetable only: this bus has not departed yet." in rendered
    assert "No additional buses are scheduled in this direction today." in rendered
