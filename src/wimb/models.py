"""Typed domain data, independent of transport and CLI presentation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class StopStatus(Enum):
    INCOMING_AT = "incoming_at"
    STOPPED_AT = "stopped_at"
    IN_TRANSIT_TO = "in_transit_to"
    UNKNOWN = "unknown"


class TrackingStatus(Enum):
    TRACKED = "tracked"
    NOT_DEPARTED = "not_departed"
    UNAVAILABLE = "unavailable"


class DataStatus(Enum):
    LIVE = "live"
    NO_SERVICE = "no_service"
    NO_LIVE_VEHICLES = "no_live_vehicles"
    NO_USABLE_REALTIME_DATA = "no_usable_realtime_data"


@dataclass(frozen=True)
class Stop:
    stop_id: str
    name: str
    latitude: float | None = None


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
class ScheduledTrip:
    route_id: str
    direction_id: int | None
    service_id: str
    headsign: str | None


@dataclass(frozen=True)
class ScheduledRun:
    stop_time: ScheduledStopTime
    scheduled_departure: datetime
    scheduled_start: datetime
    service_date: date
    run_number: int
    run_total: int
    direction_label: str


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
    timestamp: datetime | None = None
    service_date: date | None = None


@dataclass(frozen=True)
class VehiclePosition:
    trip_id: str
    vehicle_id: str | None
    stop_id: str | None
    current_stop_sequence: int | None
    status: StopStatus
    timestamp: datetime | None
    service_date: date | None = None


@dataclass(frozen=True)
class BusFact:
    trip_id: str
    service_date: date
    run_number: int
    run_total: int
    direction_label: str
    vehicle_id: str | None
    scheduled_departure: datetime
    deviation_seconds: int | None
    as_of_stop: Stop | None
    as_of_stop_sequence: int | None
    observed_at: datetime | None
    tracking_status: TrackingStatus = TrackingStatus.TRACKED

    @property
    def estimated_arrival(self) -> datetime | None:
        if self.deviation_seconds is None:
            return None
        return self.scheduled_departure + timedelta(seconds=self.deviation_seconds)


@dataclass(frozen=True)
class RouteSnapshot:
    route_id: str
    direction_label: str
    selected_stop: Stop
    buses: tuple[BusFact, ...]
    fetched_at: datetime
    no_additional_buses: bool = False
    data_status: DataStatus = DataStatus.LIVE
    realtime_checked: bool = True


@dataclass(frozen=True)
class ProgressEvidence:
    trip_id: str
    service_date: date
    stop_sequence: int
    stop: Stop
    delay_seconds: int
    observed_at: datetime | None
