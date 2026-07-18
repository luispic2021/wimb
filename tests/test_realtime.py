from __future__ import annotations

from google.transit import gtfs_realtime_pb2

from wimb.models import StopStatus
from wimb.realtime import trip_updates, vehicle_positions


def test_parses_protobuf_trip_update_and_vehicle_position_fixture() -> None:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    trip_entity = feed.entity.add()
    trip_entity.trip_update.trip.trip_id = "trip-154"
    trip_entity.trip_update.trip.route_id = "154"
    trip_entity.trip_update.vehicle.id = "1204"
    stop_update = trip_entity.trip_update.stop_time_update.add()
    stop_update.stop_sequence = 2
    stop_update.stop_id = "B"
    stop_update.departure.delay = 240
    vehicle_entity = feed.entity.add()
    vehicle_entity.vehicle.trip.trip_id = "trip-154"
    vehicle_entity.vehicle.vehicle.id = "1204"
    vehicle_entity.vehicle.current_stop_sequence = 3
    vehicle_entity.vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.IN_TRANSIT_TO

    parsed_updates = trip_updates(feed)
    parsed_positions = vehicle_positions(feed)

    assert parsed_updates[0].stop_updates[0].departure_delay_seconds == 240
    assert parsed_positions[0].status is StopStatus.IN_TRANSIT_TO
    assert parsed_positions[0].stop_id is None
