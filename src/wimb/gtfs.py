"""Static GTFS caching and lookup, with no presentation concerns."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import TextIOWrapper
from pathlib import Path

from .client import TransitDataClient
from .models import ScheduledRun, ScheduledStopTime, ScheduledTrip, Stop

CACHE_TTL = timedelta(days=7)


def _parse_gtfs_time(value: str) -> timedelta:
    hour, minute, second = (int(piece) for piece in value.split(":"))
    return timedelta(hours=hour, minutes=minute, seconds=second)


@dataclass
class GtfsStore:
    archive_path: Path
    routes: dict[str, str]
    stops: dict[str, Stop]
    trips: dict[str, ScheduledTrip]
    stop_times: dict[str, list[ScheduledStopTime]]
    calendar: dict[str, dict[str, str]]
    calendar_dates: dict[tuple[str, date], int]

    @classmethod
    def cached(
        cls, client: TransitDataClient, cache_dir: Path, operator_id: str, now: datetime
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
        stops = {
            row["stop_id"]: Stop(
                row["stop_id"],
                row["stop_name"],
                float(row["stop_lat"]) if row.get("stop_lat") else None,
            )
            for row in stop_rows
        }
        trips = {
            row["trip_id"]: ScheduledTrip(
                route_id=row["route_id"],
                direction_id=int(row["direction_id"]) if row.get("direction_id") else None,
                service_id=row["service_id"],
                headsign=row.get("trip_headsign") or None,
            )
            for row in trip_rows
        }
        stop_times: dict[str, list[ScheduledStopTime]] = {}
        for row in stop_time_rows:
            trip = trips.get(row["trip_id"])
            if trip is None:
                continue
            stop_times.setdefault(row["trip_id"], []).append(
                ScheduledStopTime(
                    row["trip_id"],
                    trip.route_id,
                    trip.direction_id,
                    trip.service_id,
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

    def scheduled_runs_at_stop(
        self, route_id: str, stop_id: str, direction_id: int | None, now: datetime
    ) -> list[ScheduledRun]:
        """Return stop times with stable run identities for nearby service dates.

        The look-back is based on the largest GTFS departure offset, so a 25:10
        departure is still associated with the previous service date at 01:10.
        """
        largest_offset = max(
            (
                entry.departure_offset
                for entries in self.stop_times.values()
                for entry in entries
                if entry.route_id == route_id
            ),
            default=timedelta(),
        )
        lookback_days = max(1, largest_offset.days)
        results: list[ScheduledRun] = []
        for day_offset in range(-lookback_days, 2):
            service_date = now.date() + timedelta(days=day_offset)
            identities = self._run_identities(route_id, service_date)
            for trip_id, (run_number, run_total) in identities.items():
                trip = self.trips[trip_id]
                if direction_id is not None and trip.direction_id != direction_id:
                    continue
                stop_time = next(
                    (
                        entry
                        for entry in self.stop_times.get(trip_id, [])
                        if entry.stop_id == stop_id
                    ),
                    None,
                )
                if stop_time is None:
                    continue
                departure = (
                    datetime.combine(service_date, time(tzinfo=now.tzinfo))
                    + stop_time.departure_offset
                )
                scheduled_start = datetime.combine(service_date, time(tzinfo=now.tzinfo)) + min(
                    entry.departure_offset for entry in self.stop_times[trip_id]
                )
                results.append(
                    ScheduledRun(
                        stop_time=stop_time,
                        scheduled_departure=departure,
                        scheduled_start=scheduled_start,
                        service_date=service_date,
                        run_number=run_number,
                        run_total=run_total,
                        direction_label=self.direction_label(route_id, trip.direction_id),
                    )
                )
        return sorted(results, key=lambda run: run.scheduled_departure)

    def _run_identities(self, route_id: str, service_date: date) -> dict[str, tuple[int, int]]:
        groups: dict[int | None, list[tuple[timedelta, str]]] = {}
        for trip_id, trip in self.trips.items():
            entries = self.stop_times.get(trip_id, [])
            if (
                trip.route_id != route_id
                or not entries
                or not self.service_runs(trip.service_id, service_date)
            ):
                continue
            anchor = min(entry.departure_offset for entry in entries)
            groups.setdefault(trip.direction_id, []).append((anchor, trip_id))

        identities: dict[str, tuple[int, int]] = {}
        for runs in groups.values():
            ordered = sorted(runs, key=lambda item: (item[0], item[1]))
            for index, (_anchor, trip_id) in enumerate(ordered, start=1):
                identities[trip_id] = (index, len(ordered))
        return identities

    def direction_label(self, route_id: str, direction_id: int | None) -> str:
        """Describe a direction from trip geography and destination, not ID meaning."""
        matching = [
            (trip_id, trip)
            for trip_id, trip in self.trips.items()
            if trip.route_id == route_id and trip.direction_id == direction_id
        ]
        headsigns = [trip.headsign for _trip_id, trip in matching if trip.headsign]
        destinations = [
            self.stops[entries[-1].stop_id].name
            for trip_id, _trip in matching
            if (entries := self.stop_times.get(trip_id, [])) and entries[-1].stop_id in self.stops
        ]
        destination = _most_common(headsigns) or _most_common(destinations) or "unknown destination"
        latitude_changes: list[float] = []
        for trip_id, _trip in matching:
            entries = self.stop_times.get(trip_id, [])
            if not entries:
                continue
            first = self.stops.get(entries[0].stop_id)
            last = self.stops.get(entries[-1].stop_id)
            if first and last and first.latitude is not None and last.latitude is not None:
                latitude_changes.append(last.latitude - first.latitude)
        net_change = sum(latitude_changes)
        if net_change < 0:
            return f"Southbound to {destination}"
        if net_change > 0:
            return f"Northbound to {destination}"
        return f"To {destination}"

    def route_directions(self, route_id: str) -> list[tuple[int, str]]:
        direction_ids = sorted(
            {
                trip.direction_id
                for trip in self.trips.values()
                if trip.route_id == route_id and trip.direction_id is not None
            }
        )
        return [
            (direction_id, self.direction_label(route_id, direction_id))
            for direction_id in direction_ids
        ]

    def route_stops(self, route_id: str, direction_id: int) -> list[Stop]:
        """Return unique stops in their earliest published sequence order."""
        earliest_sequences: dict[str, int] = {}
        for trip_id, trip in self.trips.items():
            if trip.route_id != route_id or trip.direction_id != direction_id:
                continue
            for stop_time in self.stop_times.get(trip_id, []):
                current = earliest_sequences.get(stop_time.stop_id)
                earliest_sequences[stop_time.stop_id] = (
                    stop_time.stop_sequence
                    if current is None
                    else min(current, stop_time.stop_sequence)
                )
        ordered_ids = sorted(earliest_sequences, key=lambda item: (earliest_sequences[item], item))
        return [self.stops[stop_id] for stop_id in ordered_ids if stop_id in self.stops]

    def stop_sequence(self, trip_id: str, stop_id: str) -> int | None:
        for item in self.stop_times.get(trip_id, []):
            if item.stop_id == stop_id:
                return item.stop_sequence
        return None

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


def _most_common(values: list[str]) -> str | None:
    return Counter(values).most_common(1)[0][0] if values else None
