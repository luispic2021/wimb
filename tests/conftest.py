from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from wimb.gtfs import GtfsStore


@pytest.fixture
def gtfs_store(tmp_path: Path) -> GtfsStore:
    fixture_dir = Path(__file__).parent / "fixtures"
    archive_path = tmp_path / "fixture-gtfs.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for filename in (
            "routes.txt",
            "stops.txt",
            "trips.txt",
            "stop_times.txt",
            "calendar.txt",
            "calendar_dates.txt",
        ):
            archive.write(fixture_dir / filename, filename)
    return GtfsStore.from_zip(archive_path)
