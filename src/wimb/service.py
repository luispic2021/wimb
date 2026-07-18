"""Application service coordinating 511 transport and WIMB's domain layer."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .client import TransitClient, feed_timestamp
from .deviation import build_bus_facts
from .errors import ApiError, NoLiveVehiclesError, NoUsableRealtimeDataError, StaleFeedError
from .gtfs import GtfsStore
from .models import RouteSnapshot
from .realtime import trip_updates, vehicle_positions

OPERATOR_NAME = "Golden Gate Transit"
ROUTE_ID = "154"
OPERATOR_CACHE_TTL = timedelta(days=7)
PACIFIC = ZoneInfo("America/Los_Angeles")


class WimbService:
    def __init__(self, client: TransitClient, cache_dir: Path, stale_after_seconds: int) -> None:
        self._client = client
        self._cache_dir = cache_dir
        self._stale_after_seconds = stale_after_seconds

    def list_stops(self) -> list[tuple[str, str]]:
        now = datetime.now(PACIFIC)
        operator_id = self._operator_id(now)
        gtfs = GtfsStore.cached(self._client, self._cache_dir, operator_id, now)
        stop_ids = {
            item.stop_id
            for trip_id, items in gtfs.stop_times.items()
            if gtfs.trips[trip_id][0] == ROUTE_ID
            for item in items
        }
        return sorted(
            (stop_id, gtfs.stops[stop_id].name) for stop_id in stop_ids if stop_id in gtfs.stops
        )

    def snapshot(
        self, stop_id: str, direction_id: int | None, count: int, now: datetime | None = None
    ) -> RouteSnapshot:
        current_time = now or datetime.now(PACIFIC)
        operator_id = self._operator_id(current_time)
        gtfs = GtfsStore.cached(self._client, self._cache_dir, operator_id, current_time)
        selected_stop = gtfs.stops.get(stop_id)
        if selected_stop is None:
            raise ApiError(
                f"Stop {stop_id!r} is not in Golden Gate Transit GTFS. Run --list-stops."
            )
        upcoming = gtfs.upcoming_at_stop(ROUTE_ID, stop_id, direction_id, current_time)
        trip_feed = self._client.fetch_trip_updates(operator_id)
        vehicle_feed = self._client.fetch_vehicle_positions(operator_id)
        self._assert_fresh(trip_feed, "TripUpdates", current_time)
        self._assert_fresh(vehicle_feed, "VehiclePositions", current_time)
        updates = [item for item in trip_updates(trip_feed) if item.route_id in (ROUTE_ID, None)]
        positions = [
            item
            for item in vehicle_positions(vehicle_feed)
            if item.trip_id in gtfs.trips and gtfs.trips[item.trip_id][0] == ROUTE_ID
        ]
        if not positions:
            raise NoLiveVehiclesError("No live vehicles on route 154 right now.")
        buses = build_bus_facts(gtfs, upcoming, updates, positions)[:count]
        if not buses:
            raise NoUsableRealtimeDataError(
                "Live route-154 vehicles exist, but 511 has no current-stop delay fact to display."
            )
        return RouteSnapshot(
            ROUTE_ID, gtfs.routes[ROUTE_ID], selected_stop, tuple(buses), current_time
        )

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
