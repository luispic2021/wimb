"""FastAPI application and versioned HTTP contract."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..client import TransitClient
from ..config import load_dotenv, load_settings
from ..errors import (
    ApiAuthenticationError,
    ApiError,
    ApiUnavailableError,
    ConfigurationError,
    InvalidDirectionError,
    InvalidStopError,
    NoLiveVehiclesError,
    NoUsableRealtimeDataError,
    StaleFeedError,
    WimbError,
)
from ..models import BusFact, RouteSnapshot, Stop, TrackingStatus
from ..realtime_cache import CachedTransitClient
from ..service import PACIFIC, ROUTE_ID, WimbService
from .schemas import (
    BusStatus,
    DirectionsResponse,
    DirectionSummary,
    ErrorDetail,
    ErrorResponse,
    FeedStatus,
    HealthResponse,
    ReadinessResponse,
    RoutesResponse,
    RouteSummary,
    StatusResponse,
    StopsResponse,
    StopSummary,
)

LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
ROUTE_NAME_FALLBACK = "Novato – San Francisco"
STATUS_BUS_COUNT = 2

RESOURCE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Invalid route, direction, or stop."},
    422: {"model": ErrorResponse, "description": "Invalid request parameters."},
    500: {"model": ErrorResponse, "description": "Unexpected internal failure."},
    502: {"model": ErrorResponse, "description": "511 request failure."},
    503: {"model": ErrorResponse, "description": "Server or realtime data unavailable."},
}
STATUS_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    **RESOURCE_ERROR_RESPONSES,
    409: {"model": ErrorResponse, "description": "Realtime evidence is unavailable."},
}


class WebService(Protocol):
    def route_name(self) -> str: ...

    def directions(self) -> list[tuple[int, str]]: ...

    def stops_for_direction(self, direction_id: int) -> list[Stop]: ...

    def snapshot(
        self, stop_id: str, direction_id: int | None, count: int, now: datetime | None = None
    ) -> RouteSnapshot: ...


@dataclass(frozen=True)
class WebRuntime:
    service: WebService | None
    readiness_error: str | None = None


class InvalidRouteError(WimbError):
    """Only Route 154 is supported in this milestone."""


def build_runtime(config_path: Path = Path("wimb.toml")) -> WebRuntime:
    """Build the web service without making a 511 request."""
    try:
        load_dotenv(Path(".env"))
        settings = load_settings(config_path, None, None, None)
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=settings.cache_dir) as probe:
            probe.write(b"wimb-readiness")
            probe.flush()
    except (ConfigurationError, OSError) as error:
        return WebRuntime(None, _safe_configuration_message(error))
    client = CachedTransitClient(TransitClient(settings.api_key))
    return WebRuntime(
        WimbService(client, settings.cache_dir, settings.stale_after_seconds),
    )


def create_app(
    runtime: WebRuntime | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    app = FastAPI(
        title="WIMB API",
        version="1.0.0",
        description=(
            "Route 154 timetable identity and transparent arrival estimates based on "
            "confirmed realtime schedule deviation evidence."
        ),
    )
    app.state.runtime = runtime or build_runtime()
    app.state.clock = clock or (lambda: datetime.now(PACIFIC))
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
    _install_error_handlers(app)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health", response_model=HealthResponse, tags=["operations"])
    def health(request: Request) -> HealthResponse:
        return HealthResponse(status="ok", generated_at=_now(request))

    @app.get(
        "/ready",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["operations"],
    )
    def ready(request: Request) -> ReadinessResponse | JSONResponse:
        current = _runtime(request)
        generated_at = _now(request)
        if current.service is None:
            response = ReadinessResponse(
                status="not_ready",
                configured=False,
                detail=current.readiness_error or "Required server configuration is unavailable.",
                generated_at=generated_at,
            )
            return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
        return ReadinessResponse(
            status="ready",
            configured=True,
            detail="Required server configuration and cache storage are available.",
            generated_at=generated_at,
        )

    @app.get("/api/v1/routes", response_model=RoutesResponse, tags=["Route 154"])
    def routes() -> RoutesResponse:
        return RoutesResponse(routes=[RouteSummary(route_id=ROUTE_ID, name=ROUTE_NAME_FALLBACK)])

    @app.get(
        "/api/v1/routes/{route_id}/directions",
        response_model=DirectionsResponse,
        responses=RESOURCE_ERROR_RESPONSES,
        tags=["Route 154"],
    )
    def directions(route_id: str, request: Request) -> DirectionsResponse:
        _assert_route(route_id)
        values = _service(request).directions()
        return DirectionsResponse(
            route_id=ROUTE_ID,
            directions=[
                DirectionSummary(direction_id=direction_id, label=label)
                for direction_id, label in values
            ],
        )

    @app.get(
        "/api/v1/routes/{route_id}/stops",
        response_model=StopsResponse,
        responses=RESOURCE_ERROR_RESPONSES,
        tags=["Route 154"],
    )
    def stops(
        route_id: str,
        request: Request,
        direction_id: int = Query(ge=0),
    ) -> StopsResponse:
        _assert_route(route_id)
        service = _service(request)
        direction_label = _direction_label(service, direction_id)
        values = service.stops_for_direction(direction_id)
        return StopsResponse(
            route_id=ROUTE_ID,
            direction_id=direction_id,
            direction_label=direction_label,
            stops=[
                StopSummary(stop_id=stop.stop_id, name=stop.name, sequence=index)
                for index, stop in enumerate(values, start=1)
            ],
        )

    @app.get(
        "/api/v1/routes/{route_id}/status",
        response_model=StatusResponse,
        responses=STATUS_ERROR_RESPONSES,
        tags=["Route 154"],
    )
    def status(
        route_id: str,
        request: Request,
        direction_id: int = Query(ge=0),
        stop_id: str = Query(min_length=1),
    ) -> StatusResponse:
        _assert_route(route_id)
        service = _service(request)
        direction_label = _direction_label(service, direction_id)
        snapshot = service.snapshot(stop_id, direction_id, STATUS_BUS_COUNT)
        return _status_response(service, snapshot, direction_id, direction_label)

    return app


def _status_response(
    service: WebService,
    snapshot: RouteSnapshot,
    direction_id: int,
    direction_label: str,
) -> StatusResponse:
    return StatusResponse(
        route_id=snapshot.route_id,
        route_name=service.route_name(),
        direction_id=direction_id,
        direction_label=direction_label,
        stop_id=snapshot.selected_stop.stop_id,
        stop_name=snapshot.selected_stop.name,
        buses=[_bus_status(bus, snapshot.fetched_at) for bus in snapshot.buses],
        no_additional_buses=snapshot.no_additional_buses,
        data_status=snapshot.data_status.value,
        feed_status=(FeedStatus.FRESH if snapshot.realtime_checked else FeedStatus.NOT_REQUESTED),
        generated_at=snapshot.fetched_at,
    )


def _bus_status(bus: BusFact, generated_at: datetime) -> BusStatus:
    observation_age = (
        max(0, int((generated_at - bus.observed_at).total_seconds()))
        if bus.observed_at is not None
        else None
    )
    return BusStatus(
        run_number=bus.run_number,
        run_total=bus.run_total,
        scheduled_time=bus.scheduled_departure,
        estimated_arrival=bus.estimated_arrival,
        tracking_status=bus.tracking_status.value,
        deviation_seconds=bus.deviation_seconds,
        deviation_label=_deviation_label(bus),
        evidence_stop_id=bus.as_of_stop.stop_id if bus.as_of_stop else None,
        evidence_stop_name=bus.as_of_stop.name if bus.as_of_stop else None,
        observed_at=bus.observed_at,
        observation_age_seconds=observation_age,
        freshness=_freshness_label(observation_age),
        vehicle_id=bus.vehicle_id,
    )


def _deviation_label(bus: BusFact) -> str:
    if bus.deviation_seconds is None:
        if bus.tracking_status is TrackingStatus.NOT_DEPARTED:
            return "Not departed"
        return "Live tracking unavailable"
    if abs(bus.deviation_seconds) <= 59:
        return "ON TIME"
    amount = _duration_label(abs(bus.deviation_seconds))
    return f"{amount} {'behind' if bus.deviation_seconds > 0 else 'ahead'}"


def _duration_label(seconds: int) -> str:
    minutes, remainder = divmod(seconds, 60)
    minute_unit = "minute" if minutes == 1 else "minutes"
    second_unit = "second" if remainder == 1 else "seconds"
    return f"{minutes} {minute_unit} and {remainder:02d} {second_unit}"


def _freshness_label(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "Observation time unavailable"
    if age_seconds < 60:
        return f"Updated {age_seconds} seconds ago"
    return f"Updated {age_seconds // 60} minutes ago"


def _assert_route(route_id: str) -> None:
    if route_id != ROUTE_ID:
        raise InvalidRouteError(f"Route {route_id!r} is not supported; use Route {ROUTE_ID}.")


def _direction_label(service: WebService, direction_id: int) -> str:
    labels = dict(service.directions())
    try:
        return labels[direction_id]
    except KeyError as error:
        raise InvalidDirectionError(
            f"Direction {direction_id} is not published for Route {ROUTE_ID}."
        ) from error


def _service(request: Request) -> WebService:
    current = _runtime(request)
    if current.service is None:
        raise ConfigurationError(
            current.readiness_error or "Required server configuration is unavailable."
        )
    return current.service


def _runtime(request: Request) -> WebRuntime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def _now(request: Request) -> datetime:
    return request.app.state.clock()  # type: ignore[no-any-return]


def _safe_configuration_message(error: Exception) -> str:
    if isinstance(error, ConfigurationError):
        return str(error)
    return "The configured cache directory is not writable."


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    def validation_error(_request: Request, _error_value: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "The request parameters are invalid.")

    @app.exception_handler(InvalidRouteError)
    def invalid_route(_request: Request, error: InvalidRouteError) -> JSONResponse:
        return _error(404, "invalid_route", str(error))

    @app.exception_handler(InvalidDirectionError)
    def invalid_direction(_request: Request, error: InvalidDirectionError) -> JSONResponse:
        return _error(404, "invalid_direction", str(error))

    @app.exception_handler(InvalidStopError)
    def invalid_stop(_request: Request, error: InvalidStopError) -> JSONResponse:
        return _error(404, "invalid_stop", str(error))

    @app.exception_handler(StaleFeedError)
    def stale_feed(_request: Request, _error_value: StaleFeedError) -> JSONResponse:
        return _error(
            503,
            "realtime_feed_stale",
            "511 realtime data is stale; WIMB will not present it as live.",
        )

    @app.exception_handler(ApiAuthenticationError)
    def authentication_error(
        _request: Request, _error_value: ApiAuthenticationError
    ) -> JSONResponse:
        return _error(
            503,
            "upstream_authentication_failed",
            "The server could not authenticate with 511.",
        )

    @app.exception_handler(ApiUnavailableError)
    def unavailable(_request: Request, _error_value: ApiUnavailableError) -> JSONResponse:
        return _error(
            503,
            "upstream_temporarily_unavailable",
            "511 is temporarily unavailable. Try again shortly.",
        )

    @app.exception_handler(NoLiveVehiclesError)
    def no_live_vehicles(_request: Request, _error_value: NoLiveVehiclesError) -> JSONResponse:
        return _error(409, "no_live_vehicles", "No live Route 154 vehicle is available.")

    @app.exception_handler(NoUsableRealtimeDataError)
    def no_usable_data(_request: Request, _error_value: NoUsableRealtimeDataError) -> JSONResponse:
        return _error(
            409,
            "no_defensible_deviation",
            "A vehicle exists, but no defensible deviation fact is available.",
        )

    @app.exception_handler(ConfigurationError)
    def configuration_error(_request: Request, _error_value: ConfigurationError) -> JSONResponse:
        return _error(
            503,
            "server_not_ready",
            "Required server configuration is unavailable.",
        )

    @app.exception_handler(ApiError)
    def api_error(_request: Request, _error_value: ApiError) -> JSONResponse:
        return _error(502, "upstream_request_failed", "511 could not fulfil the request.")

    @app.exception_handler(Exception)
    def unexpected_error(_request: Request, error: Exception) -> JSONResponse:
        LOGGER.error("Unhandled web request exception type: %s", type(error).__name__)
        return _error(500, "internal_error", "An unexpected internal error occurred.")


app = create_app()
