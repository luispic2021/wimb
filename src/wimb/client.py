"""Small transport client for the 511 Open Data API."""

from __future__ import annotations

import gzip
import json
import logging
from datetime import UTC, datetime
from io import BytesIO
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from google.transit import gtfs_realtime_pb2  # type: ignore[import-untyped]

from .errors import ApiAuthenticationError, ApiError, ApiUnavailableError

LOGGER = logging.getLogger(__name__)
BASE_URL = "https://api.511.org/transit"


class TransitClient:
    def __init__(self, api_key: str, timeout_seconds: int = 30) -> None:
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def fetch_operators(self) -> list[dict[str, Any]]:
        response = self._get("operators", {"format": "json"}, "application/json")
        decoded = json.loads(response)
        if not isinstance(decoded, list):
            raise ApiError("511 operators response was not a list.")
        return [entry for entry in decoded if isinstance(entry, dict)]

    def fetch_gtfs(self, operator_id: str) -> bytes:
        return self._get(
            "datafeeds", {"operator_id": operator_id, "status": "active"}, "application/zip"
        )

    def fetch_trip_updates(self, operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self._parse_feed(
            self._get("tripupdates", {"agency": operator_id}, "application/x-google-protobuf")
        )

    def fetch_vehicle_positions(self, operator_id: str) -> gtfs_realtime_pb2.FeedMessage:
        return self._parse_feed(
            self._get("vehiclepositions", {"agency": operator_id}, "application/x-google-protobuf")
        )

    def _get(self, endpoint: str, params: dict[str, str], accept: str) -> bytes:
        query = urlencode({"api_key": self._api_key, **params})
        request = Request(
            f"{BASE_URL}/{endpoint}?{query}", headers={"Accept": accept, "Accept-Encoding": "gzip"}
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310 - fixed API base URL
                body = cast(bytes, response.read())
                if response.headers.get("Content-Encoding") == "gzip":
                    return gzip.GzipFile(fileobj=BytesIO(body)).read()
                return body
        except HTTPError as error:
            if error.code in (401, 403):
                raise ApiAuthenticationError("511 rejected WIMB_API_KEY.") from error
            if error.code >= 500:
                raise ApiUnavailableError(f"511 is unavailable (HTTP {error.code}).") from error
            raise ApiError(f"511 request failed (HTTP {error.code}).") from error
        except URLError as error:
            raise ApiUnavailableError("511 could not be reached.") from error

    @staticmethod
    def _parse_feed(payload: bytes) -> gtfs_realtime_pb2.FeedMessage:
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(payload)
        return feed


def feed_timestamp(feed: gtfs_realtime_pb2.FeedMessage) -> datetime | None:
    timestamp = feed.header.timestamp
    return datetime.fromtimestamp(timestamp, UTC) if timestamp else None
