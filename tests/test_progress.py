from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from wimb.models import BusFact, Stop
from wimb.progress import ProgressCheckpointStore


def _bus(sequence: int, observed_at: datetime) -> BusFact:
    return BusFact(
        trip_id="trip-154",
        service_date=date(2026, 7, 20),
        run_number=6,
        run_total=7,
        direction_label="Southbound to San Francisco",
        vehicle_id="964",
        scheduled_departure=datetime(2026, 7, 20, 7, 57, tzinfo=UTC),
        deviation_seconds=sequence * 10,
        as_of_stop=Stop(str(sequence), f"Stop {sequence}"),
        as_of_stop_sequence=sequence,
        observed_at=observed_at,
    )


def test_checkpoint_keeps_furthest_progress_across_updates(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    store = ProgressCheckpointStore(tmp_path)

    store.update([_bus(21, now)], now)
    store.update([_bus(20, now + timedelta(minutes=1))], now)

    loaded = store.load(now)
    evidence = loaded[(date(2026, 7, 20), "trip-154")]
    assert evidence.stop_sequence == 21
    assert evidence.stop.name == "Stop 21"
    assert evidence.delay_seconds == 210


def test_checkpoint_replaces_same_stop_with_fresher_evidence(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    store = ProgressCheckpointStore(tmp_path)

    store.update([_bus(21, now)], now)
    store.update([_bus(21, now + timedelta(seconds=30))], now)

    evidence = store.load(now)[(date(2026, 7, 20), "trip-154")]
    assert evidence.observed_at == now + timedelta(seconds=30)


def test_checkpoint_expires_old_service_dates(tmp_path: Path) -> None:
    original_now = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    store = ProgressCheckpointStore(tmp_path)
    store.update([_bus(21, original_now)], original_now)

    assert store.load(original_now + timedelta(days=3)) == {}


def test_invalid_checkpoint_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "route-progress.json").write_text("not-json", encoding="utf-8")

    loaded = ProgressCheckpointStore(tmp_path).load(datetime(2026, 7, 20, 8, 0, tzinfo=UTC))

    assert loaded == {}
