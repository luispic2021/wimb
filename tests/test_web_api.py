from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wimb.config import load_dotenv
from wimb.errors import (
    ApiAuthenticationError,
    ApiError,
    ApiUnavailableError,
    ConfigurationError,
    InvalidStopError,
    NoLiveVehiclesError,
    NoUsableRealtimeDataError,
    StaleFeedError,
)
from wimb.models import BusFact, DataStatus, RouteSnapshot, Stop, TrackingStatus
from wimb.web import app as app_module
from wimb.web.app import WebRuntime, build_runtime, create_app

NOW = datetime.fromisoformat("2026-07-20T08:00:00-07:00")


class _Service:
    def route_name(self) -> str:
        return "Novato - San Francisco"

    def directions(self) -> list[tuple[int, str]]:
        return [
            (0, "Northbound to Novato"),
            (1, "Southbound to San Francisco Financial District"),
        ]

    def stops_for_direction(self, direction_id: int) -> list[Stop]:
        if direction_id == 0:
            return [Stop("40057", "Mission St & 4th St"), Stop("A", "Novato")]
        return [Stop("40581", "N San Pedro Rd Bus Pad"), Stop("C", "San Francisco")]

    def snapshot(
        self,
        stop_id: str,
        direction_id: int | None,
        count: int,
        now: datetime | None = None,
    ) -> RouteSnapshot:
        if stop_id == "invalid":
            raise InvalidStopError("Stop 'invalid' is not served by Route 154 in direction 1.")
        assert direction_id == 1
        assert count == 2
        bus = BusFact(
            trip_id="trip-6",
            service_date=NOW.date(),
            run_number=6,
            run_total=7,
            direction_label="Southbound to San Francisco Financial District",
            vehicle_id="964",
            scheduled_departure=NOW + timedelta(minutes=10),
            deviation_seconds=330,
            as_of_stop=Stop("40200", "Terra Linda Bus Pad"),
            as_of_stop_sequence=12,
            observed_at=NOW - timedelta(seconds=14),
            tracking_status=TrackingStatus.TRACKED,
        )
        return RouteSnapshot(
            "154",
            bus.direction_label,
            Stop(stop_id, "N San Pedro Rd Bus Pad"),
            (bus,),
            NOW,
            False,
            DataStatus.LIVE,
        )


class _FailingService(_Service):
    def __init__(self, error: Exception) -> None:
        self.error = error

    def snapshot(
        self,
        stop_id: str,
        direction_id: int | None,
        count: int,
        now: datetime | None = None,
    ) -> RouteSnapshot:
        raise self.error


class _StateService(_Service):
    def __init__(self, data_status: DataStatus) -> None:
        self.data_status = data_status

    def snapshot(
        self,
        stop_id: str,
        direction_id: int | None,
        count: int,
        now: datetime | None = None,
    ) -> RouteSnapshot:
        return RouteSnapshot(
            "154",
            "Southbound to San Francisco Financial District",
            Stop(stop_id, "N San Pedro Rd Bus Pad"),
            (),
            NOW,
            True,
            self.data_status,
            self.data_status is not DataStatus.NO_SERVICE,
        )


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(WebRuntime(_Service()), clock=lambda: NOW))


def test_health_does_not_require_configuration_or_511() -> None:
    client = TestClient(create_app(WebRuntime(None, "WIMB_API_KEY is not set."), clock=lambda: NOW))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "generated_at": NOW.isoformat()}


def test_readiness_reports_configured_and_missing_states(client: TestClient) -> None:
    ready = client.get("/ready")
    missing_client = TestClient(
        create_app(WebRuntime(None, "WIMB_API_KEY is not set."), clock=lambda: NOW)
    )
    missing = missing_client.get("/ready")

    assert ready.status_code == 200
    assert ready.json()["configured"] is True
    assert missing.status_code == 503
    assert missing.json()["status"] == "not_ready"
    assert missing.json()["configured"] is False


def test_malformed_toml_produces_not_ready_runtime(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("direction_id = [", encoding="utf-8")

    runtime = build_runtime(config_path)

    assert runtime.service is None
    assert runtime.readiness_error == f"Could not read configuration file {config_path}."


def test_unreadable_dotenv_is_a_configuration_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.touch()

    def fail_read(_self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("not readable")

    monkeypatch.setattr(Path, "read_text", fail_read)

    with pytest.raises(ConfigurationError, match="Could not read configuration file"):
        load_dotenv(dotenv)


def test_dotenv_read_failure_produces_not_ready_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_load(_path: Path) -> None:
        raise ConfigurationError("Could not read configuration file .env.")

    monkeypatch.setattr(app_module, "load_dotenv", fail_load)

    runtime = build_runtime(tmp_path / "wimb.toml")

    assert runtime.service is None
    assert runtime.readiness_error == "Could not read configuration file .env."


def test_cache_write_probe_failure_produces_not_ready_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path / "cache"
    config_path = tmp_path / "wimb.toml"
    config_path.write_text(f'cache_dir = "{cache_dir}"\n', encoding="utf-8")
    monkeypatch.setenv("WIMB_API_KEY", "test-only-key")

    def fail_probe(*args: object, **kwargs: object) -> None:
        raise PermissionError("not writable")

    monkeypatch.setattr(app_module.tempfile, "NamedTemporaryFile", fail_probe)

    runtime = build_runtime(config_path)

    assert runtime.service is None
    assert runtime.readiness_error == "The configured cache directory is not writable."


def test_routes_directions_and_ordered_stops(client: TestClient) -> None:
    routes = client.get("/api/v1/routes")
    directions = client.get("/api/v1/routes/154/directions")
    stops = client.get("/api/v1/routes/154/stops", params={"direction_id": 1})

    assert routes.json()["routes"] == [{"route_id": "154", "name": "Novato – San Francisco"}]
    assert directions.json()["directions"][1] == {
        "direction_id": 1,
        "label": "Southbound to San Francisco Financial District",
    }
    assert stops.json() == {
        "route_id": "154",
        "direction_id": 1,
        "direction_label": "Southbound to San Francisco Financial District",
        "stops": [
            {"stop_id": "40581", "name": "N San Pedro Rd Bus Pad", "sequence": 1},
            {"stop_id": "C", "name": "San Francisco", "sequence": 2},
        ],
    }


def test_successful_status_has_explicit_schema_and_aware_timestamps(client: TestClient) -> None:
    response = client.get(
        "/api/v1/routes/154/status",
        params={"direction_id": 1, "stop_id": "40581"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_id"] == "154"
    assert payload["direction_id"] == 1
    assert payload["stop_id"] == "40581"
    assert payload["data_status"] == "live"
    assert payload["feed_status"] == "fresh"
    assert datetime.fromisoformat(payload["generated_at"]).tzinfo is not None
    bus = payload["buses"][0]
    assert bus == {
        "run_number": 6,
        "run_total": 7,
        "scheduled_time": (NOW + timedelta(minutes=10)).isoformat(),
        "estimated_arrival": (NOW + timedelta(minutes=15, seconds=30)).isoformat(),
        "tracking_status": "tracked",
        "deviation_seconds": 330,
        "deviation_label": "5 minutes and 30 seconds behind",
        "evidence_stop_id": "40200",
        "evidence_stop_name": "Terra Linda Bus Pad",
        "observed_at": (NOW - timedelta(seconds=14)).isoformat(),
        "observation_age_seconds": 14,
        "freshness": "Updated 14 seconds ago",
        "vehicle_id": "964",
    }
    assert datetime.fromisoformat(bus["scheduled_time"]).tzinfo is not None
    assert datetime.fromisoformat(bus["estimated_arrival"]).tzinfo is not None
    assert datetime.fromisoformat(bus["observed_at"]).tzinfo is not None


def test_openapi_documents_structured_error_responses(client: TestClient) -> None:
    operation = client.get("/openapi.json").json()["paths"]["/api/v1/routes/{route_id}/status"][
        "get"
    ]

    assert set(operation["responses"]) == {"200", "404", "409", "422", "500", "502", "503"}
    for status_code in ("404", "409", "422", "500", "502", "503"):
        schema = operation["responses"][status_code]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}


def test_validation_and_resource_failures_are_intentional(client: TestClient) -> None:
    missing_query = client.get("/api/v1/routes/154/status")
    invalid_route = client.get("/api/v1/routes/999/directions")
    invalid_direction = client.get("/api/v1/routes/154/stops", params={"direction_id": 9})
    invalid_stop = client.get(
        "/api/v1/routes/154/status",
        params={"direction_id": 1, "stop_id": "invalid"},
    )

    assert missing_query.status_code == 422
    assert missing_query.json()["error"]["code"] == "invalid_request"
    assert invalid_route.status_code == 404
    assert invalid_route.json()["error"]["code"] == "invalid_route"
    assert invalid_direction.status_code == 404
    assert invalid_direction.json()["error"]["code"] == "invalid_direction"
    assert invalid_stop.status_code == 404
    assert invalid_stop.json()["error"]["code"] == "invalid_stop"


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (StaleFeedError("old"), 503, "realtime_feed_stale"),
        (ApiAuthenticationError("rejected"), 503, "upstream_authentication_failed"),
        (ApiUnavailableError("down"), 503, "upstream_temporarily_unavailable"),
        (ApiError("bad response"), 502, "upstream_request_failed"),
        (NoLiveVehiclesError("none"), 409, "no_live_vehicles"),
        (NoUsableRealtimeDataError("none"), 409, "no_defensible_deviation"),
    ],
)
def test_domain_errors_map_to_distinct_http_responses(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    client = TestClient(create_app(WebRuntime(_FailingService(error)), clock=lambda: NOW))

    response = client.get(
        "/api/v1/routes/154/status",
        params={"direction_id": 1, "stop_id": "40581"},
    )

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "data_status",
    [
        DataStatus.NO_SERVICE,
        DataStatus.NO_LIVE_VEHICLES,
        DataStatus.NO_USABLE_REALTIME_DATA,
    ],
)
def test_expected_uncertainty_states_are_explicit_status_payloads(
    data_status: DataStatus,
) -> None:
    client = TestClient(create_app(WebRuntime(_StateService(data_status)), clock=lambda: NOW))

    response = client.get(
        "/api/v1/routes/154/status",
        params={"direction_id": 1, "stop_id": "40581"},
    )

    assert response.status_code == 200
    assert response.json()["data_status"] == data_status.value
    assert response.json()["feed_status"] == (
        "not_requested" if data_status is DataStatus.NO_SERVICE else "fresh"
    )


def test_api_key_is_not_leaked_by_unexpected_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "super-secret-511-key"
    client = TestClient(
        create_app(WebRuntime(_FailingService(RuntimeError(secret))), clock=lambda: NOW),
        raise_server_exceptions=False,
    )

    with caplog.at_level(logging.ERROR):
        response = client.get(
            "/api/v1/routes/154/status",
            params={"direction_id": 1, "stop_id": "40581"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert secret not in response.text
    assert secret not in caplog.text


def test_web_page_and_static_assets_are_available(client: TestClient) -> None:
    page = client.get("/")
    script = client.get("/assets/app.js")
    stylesheet = client.get("/assets/styles.css")

    assert page.status_code == 200
    assert "Where Is My Bus?" in page.text
    assert 'id="direction"' in page.text
    assert "built with &lt;3 by Luispe + Sebas" in page.text
    assert script.status_code == 200
    assert "/api/v1/routes/154/status" in script.text
    assert "ETA:" in script.text
    assert stylesheet.status_code == 200
    assert "@media (min-width: 600px)" in stylesheet.text
