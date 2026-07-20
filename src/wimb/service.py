"""Application service coordinating 511 transport and WIMB's domain layer."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import TransitDataClient, feed_timestamp
from .deviation import build_bus_listing
from .errors import ApiError, InvalidDirectionError, InvalidStopError, StaleFeedError
from .gtfs import GtfsStore
from .models import DataStatus, RouteSnapshot, Stop, TrackingStatus
from .progress import ProgressCheckpointStore
from .realtime import trip_updates, vehicle_positions

OPERATOR_NAME = "Golden Gate Transit"
ROUTE_ID = "154"
OPERATOR_CACHE_TTL = timedelta(days=7)
PACIFIC = ZoneInfo("America/Los_Angeles")


class WimbService:
    def __init__(
        self,
        client: TransitDataClient,
        cache_dir: Path,
        stale_after_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._cache_dir = cache_dir
        self._stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(PACIFIC))

    def list_stops(self) -> list[tuple[str, str]]:
        now = self._clock()
        operator_id = self._operator_id(now)
        gtfs = GtfsStore.cached(self._client, self._cache_dir, operator_id, now)
        stop_ids = {
            item.stop_id
            for trip_id, items in gtfs.stop_times.items()
            if gtfs.trips[trip_id].route_id == ROUTE_ID
            for item in items
        }
        return sorted(
            (stop_id, gtfs.stops[stop_id].name) for stop_id in stop_ids if stop_id in gtfs.stops
        )

    def route_name(self) -> str:
        now = self._clock()
        gtfs = self._gtfs(now)
        return gtfs.routes.get(ROUTE_ID, "Route 154")

    def directions(self) -> list[tuple[int, str]]:
        now = self._clock()
        return self._gtfs(now).route_directions(ROUTE_ID)

    def stops_for_direction(self, direction_id: int) -> list[Stop]:
        now = self._clock()
        gtfs = self._gtfs(now)
        valid_directions = {item[0] for item in gtfs.route_directions(ROUTE_ID)}
        if direction_id not in valid_directions:
            raise InvalidDirectionError(
                f"Direction {direction_id} is not published for Route {ROUTE_ID}."
            )
        return gtfs.route_stops(ROUTE_ID, direction_id)

    def snapshot(
        self, stop_id: str, direction_id: int | None, count: int, now: datetime | None = None
    ) -> RouteSnapshot:
        request_time = now or self._clock()
        operator_id = self._operator_id(request_time)
        gtfs = GtfsStore.cached(self._client, self._cache_dir, operator_id, request_time)
        valid_directions = {item[0] for item in gtfs.route_directions(ROUTE_ID)}
        if direction_id is not None and direction_id not in valid_directions:
            raise InvalidDirectionError(
                f"Direction {direction_id} is not published for Route {ROUTE_ID}."
            )
        selected_stop = gtfs.stops.get(stop_id)
        applicable_stops = (
            {stop.stop_id for stop in gtfs.route_stops(ROUTE_ID, direction_id)}
            if direction_id is not None
            else {
                item.stop_id
                for trip_id, items in gtfs.stop_times.items()
                if gtfs.trips[trip_id].route_id == ROUTE_ID
                for item in items
            }
        )
        if selected_stop is None or stop_id not in applicable_stops:
            qualifier = f" in direction {direction_id}" if direction_id is not None else ""
            raise InvalidStopError(
                f"Stop {stop_id!r} is not served by Route {ROUTE_ID}{qualifier}."
            )
        scheduled_runs = gtfs.scheduled_runs_at_stop(ROUTE_ID, stop_id, direction_id, request_time)
        current_service_runs = [
            run
            for run in scheduled_runs
            if run.scheduled_departure.date() == request_time.date()
            or run.service_date == request_time.date()
        ]
        if not current_service_runs:
            return RouteSnapshot(
                ROUTE_ID,
                gtfs.direction_label(ROUTE_ID, direction_id)
                if direction_id is not None
                else "All directions",
                selected_stop,
                (),
                request_time,
                True,
                DataStatus.NO_SERVICE,
            )
        trip_feed = self._client.fetch_trip_updates(operator_id)
        vehicle_feed = self._client.fetch_vehicle_positions(operator_id)
        current_time = now or self._clock()
        self._assert_fresh(trip_feed, "TripUpdates", current_time)
        self._assert_fresh(vehicle_feed, "VehiclePositions", current_time)
        updates = [item for item in trip_updates(trip_feed) if item.route_id in (ROUTE_ID, None)]
        positions = [
            item
            for item in vehicle_positions(vehicle_feed)
            if item.trip_id in gtfs.trips and gtfs.trips[item.trip_id].route_id == ROUTE_ID
        ]
        progress_store = ProgressCheckpointStore(self._cache_dir)
        prior_progress = progress_store.load(current_time)
        buses, no_additional_buses = build_bus_listing(
            gtfs,
            scheduled_runs,
            updates,
            positions,
            current_time,
            count,
            prior_progress,
        )
        progress_store.update(buses, current_time)
        if any(bus.tracking_status is TrackingStatus.TRACKED for bus in buses):
            data_status = DataStatus.LIVE
        elif not positions:
            data_status = DataStatus.NO_LIVE_VEHICLES
        else:
            data_status = DataStatus.NO_USABLE_REALTIME_DATA
        directions = {bus.direction_label for bus in buses}
        if len(directions) == 1:
            direction_label = directions.pop()
        elif direction_id is not None:
            direction_label = gtfs.direction_label(ROUTE_ID, direction_id)
        else:
            direction_label = "All directions"
        return RouteSnapshot(
            ROUTE_ID,
            direction_label,
            selected_stop,
            tuple(buses),
            current_time,
            no_additional_buses,
            data_status,
        )

    def _gtfs(self, now: datetime) -> GtfsStore:
        operator_id = self._operator_id(now)
        return GtfsStore.cached(self._client, self._cache_dir, operator_id, now)

    def _operator_id(self, now: datetime) -> str:
        cache_path = self._cache_dir / "operator.json"
        if (
            cache_path.exists()
            and now - datetime.fromtimestamp(cache_path.stat().st_mtime, now.tzinfo)
            <= OPERATOR_CACHE_TTL
        ):
            return str(json.loads(cache_path.read_text(encoding="utf-8"))["operator_id"])
        operators = self._client.fetch_operators()
        matching = [
            item
            for item in operators
            if item.get("Name") == OPERATOR_NAME and item.get("Monitored") is True
        ]
        if len(matching) != 1 or not isinstance(matching[0].get("Id"), str):
            raise ApiError(
                "511 did not return one realtime-monitored Golden Gate Transit operator."
            )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        operator_id = matching[0]["Id"]
        assert isinstance(operator_id, str)
        GtfsStore.save_operator(self._cache_dir, operator_id)
        return operator_id

    def _assert_fresh(self, feed: object, label: str, now: datetime) -> None:
        timestamp = feed_timestamp(feed)
        if timestamp is None or now - timestamp > timedelta(seconds=self._stale_after_seconds):
            raise StaleFeedError(f"{label} feed is stale; WIMB will not show it as live data.")
