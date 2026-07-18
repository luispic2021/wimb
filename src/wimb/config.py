"""Configuration loading from TOML, environment, and CLI overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError


@dataclass(frozen=True)
class Settings:
    api_key: str
    stop_id: str | None
    direction_id: int | None
    bus_count: int
    cache_dir: Path
    stale_after_seconds: int = 180


def load_settings(
    config_path: Path, stop_id: str | None, direction_id: int | None, bus_count: int | None
) -> Settings:
    values: dict[str, object] = {}
    if config_path.exists():
        with config_path.open("rb") as config_file:
            values = tomllib.load(config_file)
    api_key = os.environ.get("WIMB_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError(
            "WIMB_API_KEY is not set. Add it to .env or your shell environment."
        )
    configured_stop = values.get("stop_id")
    configured_direction = values.get("direction_id")
    configured_count = values.get("bus_count", 2)
    configured_cache = values.get("cache_dir", ".wimb")
    selected_count = bus_count if bus_count is not None else configured_count
    if not isinstance(selected_count, int) or selected_count < 1:
        raise ConfigurationError("bus_count must be a positive integer.")
    selected_direction = direction_id if direction_id is not None else configured_direction
    if selected_direction is not None and (
        not isinstance(selected_direction, int) or selected_direction < 0
    ):
        raise ConfigurationError("direction_id must be a non-negative integer.")
    if not isinstance(configured_stop, str) and configured_stop is not None:
        raise ConfigurationError("stop_id must be a string.")
    if not isinstance(configured_cache, str):
        raise ConfigurationError("cache_dir must be a path string.")
    return Settings(
        api_key=api_key,
        stop_id=stop_id or configured_stop,
        direction_id=selected_direction,
        bus_count=selected_count,
        cache_dir=Path(configured_cache),
    )
