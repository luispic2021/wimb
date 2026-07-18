"""Static GTFS caching and lookup, with no presentation concerns."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import TextIOWrapper
from pathlib import Path

from .client import TransitClient
from .models import ScheduledStopTime, Stop

CACHE_TTL = timedelta(days=7)


def _parse_gtfs_time(value: str) -> timedelta:
    hour, minute, second = (int(piece) for piece in value.split(":"))
    return timedelta(hours=hour, minutes=minute, seconds=second)


@dataclass
class GtfsStore:
    archive_path: Path
    routes: dict[str, str]
    stops: dict[str, Stop]
    trips: dict[str, tuple[str, int | None, str]]
    stop_times: dict[str, list[ScheduledStopTime]]
    calendar: dict[str, dict[str, str]]
    calendar_dates: dict[tuple[str, date], int]

    @classmethod
    def cached(
        cls, client: TransitClient, cache_dir: Path, operator_id: str, now: datetime
    ) -> GtfsStore:
        cache_dir.mkdir(parents=True, exist_ok=True)
        archive_path = cache_dir / f"{operator_id.lower()}-gtfs.zip"
        refresh_needed = (
            not archive_path.exists()
            or now - datetime.fromtimestamp(archive_path.stat().st_mtime, now.tzinfo) > CACHE_TTL
        )
        if refresh_needed:
            payload = client.fetch_gtfs(operator_id)
            with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False) as temp_file:
                temp_file.write(payload)
                temp_path = Path(temp_file.name)
            os.replace(temp_path, archive_path)
        return cls.from_zip(archive_path)

    @classmethod
    def from_zip(cls, archive_path: Path) -> GtfsStore:
        with zipfile.ZipFile(archive_path) as archive:

            def rows(filename: str) -> list[dict[str, str]]:
                with archive.open(filename) as raw_file:
                    return list(csv.DictReader(TextIOWrapper(raw_file, encoding="utf-8-sig")))

            route_rows = rows("routes.txt")
            stop_rows = rows("stops.txt")
            trip_rows = rows("trips.txt")
            stop_time_rows = rows("stop_times.txt")
            calendar_rows = rows("calendar.txt") if "calendar.txt" in archive.namelist() else []
            date_rows = (
                rows("calendar_dates.txt") if "calendar_dates.txt" in archive.namelist() else []
            )
        routes = {row["route_id"]: row["route_long_name"] for row in route_rows}
        stops = {row["stop_id"]: Stop(row["stop_id"], row["stop_name"]) for row in stop_rows}
        trips = {
            row["trip_id"]: (
                row["route_id"],
                int(row["direction_id"]) if row.get("direction_id") else None,
                row["service_id"],
            )
            for row in trip_rows
        }
        stop_times: dict[str, list[ScheduledStopTime]] = {}
        for row in stop_time_rows:
            trip = trips.get(row["trip_id"])
            if trip is None:
                continue
            route_id, direction_id, service_id = trip
            stop_times.setdefault(row["trip_id"], []).append(
                ScheduledStopTime(
                    row["trip_id"],
                    route_id,
                    direction_id,
                    service_id,
                    row["stop_id"],
                    int(row["stop_sequence"]),
                    _parse_gtfs_time(row["departure_time"]),
                )
            )
        for values in stop_times.values():
            values.sort(key=lambda item: item.stop_sequence)
        calendar = {row["service_id"]: row for row in calendar_rows}
        calendar_dates = {
            (row["service_id"], datetime.strptime(row["date"], "%Y%m%d").date()): int(
                row["exception_type"]
            )
            for row in date_rows
        }
        return cls(archive_path, routes, stops, trips, stop_times, calendar, calendar_dates)

    def service_runs(self, service_id: str, service_date: date) -> bool:
        override = self.calendar_dates.get((service_id, service_date))
        if override is not None:
            return override == 1
        rule = self.calendar.get(service_id)
        if rule is None:
            return False
        if not (
            date.fromisoformat(
                rule["start_date"][:4]
                + "-"
                + rule["start_date"][4:6]
                + "-"
                + rule["start_date"][6:]
            )
            <= service_date
            <= date.fromisoformat(
                rule["end_date"][:4] + "-" + rule["end_date"][4:6] + "-" + rule["end_date"][6:]
            )
        ):
            return False
        return (
            rule[
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[
                    service_date.weekday()
                ]
            ]
            == "1"
        )

    def upcoming_at_stop(
        self, route_id: str, stop_id: str, direction_id: int | None, now: datetime
    ) -> list[tuple[ScheduledStopTime, datetime]]:
        results: list[tuple[ScheduledStopTime, datetime]] = []
        for _trip_id, entries in self.stop_times.items():
            for entry in entries:
                if (
                    entry.stop_id != stop_id
                    or entry.route_id != route_id
                    or (direction_id is not None and entry.direction_id != direction_id)
                ):
                    continue
                for offset in (0, 1):
                    service_day = now.date() + timedelta(days=offset)
                    if not self.service_runs(entry.service_id, service_day):
                        continue
                    departure = (
                        datetime.combine(service_day, time(tzinfo=now.tzinfo))
                        + entry.departure_offset
                    )
                    if departure >= now:
                        results.append((entry, departure))
        return sorted(results, key=lambda item: item[1])

    def previous_stop(self, trip_id: str, current_sequence: int) -> Stop | None:
        candidates = [
            item
            for item in self.stop_times.get(trip_id, [])
            if item.stop_sequence < current_sequence
        ]
        if not candidates:
            return None
        return self.stops.get(candidates[-1].stop_id)

    def stop_at_sequence(self, trip_id: str, sequence: int) -> Stop | None:
        for item in self.stop_times.get(trip_id, []):
            if item.stop_sequence == sequence:
                return self.stops.get(item.stop_id)
        return None

    @staticmethod
    def save_operator(cache_dir: Path, operator_id: str) -> None:
        (cache_dir / "operator.json").write_text(
            json.dumps({"operator_id": operator_id}), encoding="utf-8"
        )
