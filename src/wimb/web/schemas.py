"""Explicit versioned API response schemas."""

from __future__ import annotations

from enum import Enum

from pydantic import AwareDatetime, BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: str
    generated_at: AwareDatetime


class ReadinessResponse(ApiModel):
    status: str
    configured: bool
    detail: str
    generated_at: AwareDatetime


class RouteSummary(ApiModel):
    route_id: str
    name: str


class RoutesResponse(ApiModel):
    routes: list[RouteSummary]


class DirectionSummary(ApiModel):
    direction_id: int
    label: str


class DirectionsResponse(ApiModel):
    route_id: str
    directions: list[DirectionSummary]


class StopSummary(ApiModel):
    stop_id: str
    name: str
    sequence: int


class StopsResponse(ApiModel):
    route_id: str
    direction_id: int
    direction_label: str
    stops: list[StopSummary]


class FeedStatus(str, Enum):
    FRESH = "fresh"
    NOT_REQUESTED = "not_requested"


class BusStatus(ApiModel):
    run_number: int
    run_total: int
    scheduled_time: AwareDatetime
    estimated_arrival: AwareDatetime | None
    tracking_status: str
    deviation_seconds: int | None
    deviation_label: str
    evidence_stop_id: str | None
    evidence_stop_name: str | None
    observed_at: AwareDatetime | None
    observation_age_seconds: int | None
    freshness: str
    vehicle_id: str | None


class StatusResponse(ApiModel):
    route_id: str
    route_name: str
    direction_id: int
    direction_label: str
    stop_id: str
    stop_name: str
    buses: list[BusStatus]
    no_additional_buses: bool
    data_status: str
    feed_status: FeedStatus
    generated_at: AwareDatetime


class ErrorDetail(ApiModel):
    code: str
    message: str


class ErrorResponse(ApiModel):
    error: ErrorDetail
