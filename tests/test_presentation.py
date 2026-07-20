from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wimb.models import BusFact, RouteSnapshot, Stop
from wimb.presentation import render_snapshot


def _snapshot(deviation: int, observed_at: datetime | None) -> RouteSnapshot:
    fetched_at = datetime(2026, 7, 13, 15, 0, tzinfo=UTC)
    bus = BusFact(
        trip_id="sb-6",
        run_number=6,
        run_total=7,
        direction_label="Southbound to San Francisco",
        vehicle_id="1204",
        scheduled_departure=datetime(2026, 7, 13, 15, 12, tzinfo=UTC),
        deviation_seconds=deviation,
        as_of_stop=Stop("A", "Lucas Valley Road"),
        observed_at=observed_at,
    )
    return RouteSnapshot(
        "154",
        "Southbound to San Francisco",
        Stop("B", "Manzanita Park & Ride"),
        (bus,),
        fetched_at,
    )


def test_human_readable_rendering_uses_run_identity_evidence_and_freshness() -> None:
    observed_at = datetime(2026, 7, 13, 15, 0, tzinfo=UTC) - timedelta(seconds=24)

    rendered = render_snapshot(_snapshot(-300, observed_at), color=False)

    assert "Route 154" in rendered
    assert "Direction: Southbound to San Francisco" in rendered
    assert "Stop: Manzanita Park & Ride" in rendered
    assert "Bus 6 of 7 · scheduled 3:12 PM" in rendered
    assert "5 min AHEAD as of Lucas Valley Road · updated 24 sec ago" in rendered
    assert "Vehicle 1204" in rendered


def test_on_time_tolerance_includes_plus_or_minus_59_seconds() -> None:
    for deviation in (-59, 0, 59):
        assert "ON TIME as of" in render_snapshot(_snapshot(deviation, None), color=False)

    assert "1 min BEHIND" in render_snapshot(_snapshot(60, None), color=False)
    assert "1 min AHEAD" in render_snapshot(_snapshot(-60, None), color=False)


def test_missing_observation_timestamp_is_reported_honestly() -> None:
    rendered = render_snapshot(_snapshot(0, None), color=False)

    assert "updated time unavailable" in rendered
