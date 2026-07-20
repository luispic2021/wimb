"""Small latest-progress checkpoint used to reject regressing realtime positions."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import BusFact, ProgressEvidence, Stop, TrackingStatus

LOGGER = logging.getLogger(__name__)
CHECKPOINT_RETENTION = timedelta(days=2)


class ProgressCheckpointStore:
    """Persist only the furthest evidence for each service-date/trip pair."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._path = cache_dir / "route-progress.json"
        self._lock_path = cache_dir / "route-progress.lock"

    def load(self, now: datetime) -> dict[tuple[date, str], ProgressEvidence]:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_SH)
                return self._read(now)
        except OSError as error:
            LOGGER.warning("WIMB progress checkpoint is unavailable: %s", error)
            return {}

    def update(self, buses: list[BusFact], now: datetime) -> None:
        evidence = [_evidence_from_bus(bus) for bus in buses]
        additions = [item for item in evidence if item is not None]
        if not additions:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+", encoding="utf-8") as lock_file:
                fcntl.flock(lock_file, fcntl.LOCK_EX)
                current = self._read(now)
                for item in additions:
                    key = (item.service_date, item.trip_id)
                    existing = current.get(key)
                    if existing is None or _is_newer_progress(item, existing):
                        current[key] = item
                self._write(current)
        except OSError as error:
            LOGGER.warning("WIMB could not update its progress checkpoint: %s", error)

    def _read(self, now: datetime) -> dict[tuple[date, str], ProgressEvidence]:
        if not self._path.exists():
            return {}
        try:
            payload: Any = json.loads(self._path.read_text(encoding="utf-8"))
            raw_entries = payload["entries"]
            if not isinstance(raw_entries, list):
                raise ValueError("entries is not a list")
            earliest = now.date() - CHECKPOINT_RETENTION
            latest = now.date() + timedelta(days=1)
            result: dict[tuple[date, str], ProgressEvidence] = {}
            for raw in raw_entries:
                item = _parse_evidence(raw)
                if earliest <= item.service_date <= latest:
                    result[(item.service_date, item.trip_id)] = item
            return result
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            LOGGER.warning("Ignoring invalid WIMB progress checkpoint: %s", error)
            return {}

    def _write(self, values: dict[tuple[date, str], ProgressEvidence]) -> None:
        payload = {
            "version": 1,
            "entries": [
                _serialize_evidence(item)
                for _key, item in sorted(values.items(), key=lambda pair: pair[0])
            ],
        }
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self._cache_dir, delete=False
        ) as temp_file:
            json.dump(payload, temp_file, separators=(",", ":"), sort_keys=True)
            temp_file.write("\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        try:
            temp_path.chmod(0o600)
            os.replace(temp_path, self._path)
        finally:
            temp_path.unlink(missing_ok=True)


def _evidence_from_bus(bus: BusFact) -> ProgressEvidence | None:
    if (
        bus.tracking_status is not TrackingStatus.TRACKED
        or bus.as_of_stop_sequence is None
        or bus.as_of_stop is None
        or bus.deviation_seconds is None
    ):
        return None
    return ProgressEvidence(
        trip_id=bus.trip_id,
        service_date=bus.service_date,
        stop_sequence=bus.as_of_stop_sequence,
        stop=bus.as_of_stop,
        delay_seconds=bus.deviation_seconds,
        observed_at=bus.observed_at,
    )


def _is_newer_progress(candidate: ProgressEvidence, existing: ProgressEvidence) -> bool:
    if candidate.stop_sequence != existing.stop_sequence:
        return candidate.stop_sequence > existing.stop_sequence
    if candidate.observed_at is None:
        return False
    return existing.observed_at is None or candidate.observed_at >= existing.observed_at


def _serialize_evidence(item: ProgressEvidence) -> dict[str, object]:
    return {
        "trip_id": item.trip_id,
        "service_date": item.service_date.isoformat(),
        "stop_sequence": item.stop_sequence,
        "stop_id": item.stop.stop_id,
        "stop_name": item.stop.name,
        "stop_latitude": item.stop.latitude,
        "delay_seconds": item.delay_seconds,
        "observed_at": item.observed_at.isoformat() if item.observed_at else None,
    }


def _parse_evidence(raw: object) -> ProgressEvidence:
    if not isinstance(raw, dict):
        raise TypeError("checkpoint entry is not an object")
    observed_value = raw.get("observed_at")
    latitude_value = raw.get("stop_latitude")
    return ProgressEvidence(
        trip_id=str(raw["trip_id"]),
        service_date=date.fromisoformat(str(raw["service_date"])),
        stop_sequence=int(raw["stop_sequence"]),
        stop=Stop(
            stop_id=str(raw["stop_id"]),
            name=str(raw["stop_name"]),
            latitude=float(latitude_value) if latitude_value is not None else None,
        ),
        delay_seconds=int(raw["delay_seconds"]),
        observed_at=datetime.fromisoformat(str(observed_value))
        if observed_value is not None
        else None,
    )
