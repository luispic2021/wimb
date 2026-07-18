"""Translate GTFS-Realtime protobuf messages into WIMB domain facts."""

from __future__ import annotations

from datetime import UTC, datetime

from google.transit import gtfs_realtime_pb2  # type: ignore[import-untyped]

from .models import RealtimeStopUpdate, StopStatus, TripUpdate, VehiclePosition


def trip_updates(feed: gtfs_realtime_pb2.FeedMessage) -> list[TripUpdate]:
    results: list[TripUpdate] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        update = entity.trip_update
        descriptor = update.trip
        updates = tuple(
            RealtimeStopUpdate(
                stop_id=item.stop_id or None,
                stop_sequence=item.stop_sequence or None,
                arrival_delay_seconds=item.arrival.delay
                if item.arrival.HasField("delay")
                else None,
                departure_delay_seconds=item.departure.delay
                if item.departure.HasField("delay")
                else None,
            )
            for item in update.stop_time_update
        )
        results.append(
            TripUpdate(
                descriptor.trip_id, descriptor.route_id or None, update.vehicle.id or None, updates
            )
        )
    return results


def vehicle_positions(feed: gtfs_realtime_pb2.FeedMessage) -> list[VehiclePosition]:
    statuses = {
        gtfs_realtime_pb2.VehiclePosition.INCOMING_AT: StopStatus.INCOMING_AT,
        gtfs_realtime_pb2.VehiclePosition.STOPPED_AT: StopStatus.STOPPED_AT,
        gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO: StopStatus.IN_TRANSIT_TO,
    }
    results: list[VehiclePosition] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        timestamp = datetime.fromtimestamp(vehicle.timestamp, UTC) if vehicle.timestamp else None
        results.append(
            VehiclePosition(
                trip_id=vehicle.trip.trip_id,
                vehicle_id=vehicle.vehicle.id or None,
                stop_id=vehicle.stop_id or None,
                current_stop_sequence=vehicle.current_stop_sequence or None,
                status=statuses.get(vehicle.current_status, StopStatus.UNKNOWN),
                timestamp=timestamp,
            )
        )
    return results
