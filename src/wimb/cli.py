"""CLI wiring for WIMB."""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path

from . import __version__
from .client import TransitClient
from .config import load_settings
from .errors import WimbError
from .presentation import clear_screen, render_snapshot, render_stops
from .service import WimbService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Where Is My Bus? Facts, not arrival predictions.")
    parser.add_argument("--config", type=Path, default=Path("wimb.toml"))
    parser.add_argument("--stop", help="GTFS stop ID; overrides wimb.toml")
    parser.add_argument("--direction", type=int, choices=(0, 1), help="GTFS direction ID")
    parser.add_argument("--count", type=int, help="Number of upcoming buses")
    parser.add_argument(
        "--list-stops", action="store_true", help="List route-154 stop IDs and exit"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Refresh and redraw every 60 seconds (requires >=120 requests/hour quota)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable diagnostic logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        _load_dotenv(Path(".env"))
        settings = load_settings(args.config, args.stop, args.direction, args.count)
        service = WimbService(
            TransitClient(settings.api_key), settings.cache_dir, settings.stale_after_seconds
        )
        if args.list_stops:
            print(render_stops(service.list_stops()))
            return 0
        if not settings.stop_id:
            raise WimbError(
                "Choose a stop with --stop STOP_ID or wimb.toml. Use --list-stops to find one."
            )
        while True:
            snapshot = service.snapshot(settings.stop_id, settings.direction_id, settings.bus_count)
            if args.watch:
                clear_screen()
            print(render_snapshot(snapshot))
            if not args.watch:
                return 0
            time.sleep(60)
    except WimbError as error:
        print(f"WIMB: {error}")
        return 2


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE local config without adding a runtime dependency."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())
