"""Typed domain data, independent of transport and CLI presentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum


class StopStatus(Enum):
    INCOMING_AT = "incoming_at"
    STOPPED_AT = "stopped_at"
    IN_TRANSIT_TO = "in_transit_to"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str


@dataclass(frozen=True)
class ScheduledStopTime:
    trip_id: str
    route_id: str
    direction_id: int | None
    service_id: str
    stop_id: str
    stop_sequence: int
    departure_offset: timedelta


@dataclass(frozen=True)
class RealtimeStopUpdate:
    stop_id: str | None
    stop_sequence: int | None
    arrival_delay_seconds: int | None
    departure_delay_seconds: int | None

    @property
    def delay_seconds(self) -> int | None:
        return (
            self.departure_delay_seconds
            if self.departure_delay_seconds is not None
            else self.arrival_delay_seconds
        )


@dataclass(frozen=True)
class TripUpdate:
    trip_id: str
    route_id: str | None
    vehicle_id: str | None
    stop_updates: tuple[RealtimeStopUpdate, ...]


@dataclass(frozen=True)
class VehiclePosition:
    trip_id: str
    vehicle_id: str | None
    stop_id: str | None
    current_stop_sequence: int | None
    status: StopStatus
    timestamp: datetime | None


@dataclass(frozen=True)
class BusFact:
    trip_id: str
    vehicle_id: str | None
    scheduled_departure: datetime
    deviation_seconds: int
    as_of_stop: Stop
    observed_at: datetime | None


@dataclass(frozen=True)
class RouteSnapshot:
    route_id: str
    destination: str
    selected_stop: Stop
    buses: tuple[BusFact, ...]
    fetched_at: datetime
