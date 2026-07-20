"""Terminal rendering only; it does not compute business facts."""

from __future__ import annotations

import sys
from datetime import datetime

from .models import RouteSnapshot

RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
ON_TIME_TOLERANCE_SECONDS = 59


def render_snapshot(snapshot: RouteSnapshot, color: bool | None = None) -> str:
    use_color = sys.stdout.isatty() if color is None else color
    lines = [
        f"Route {snapshot.route_id}",
        f"Direction: {snapshot.direction_label}",
        f"Stop: {snapshot.selected_stop.name}",
        "",
    ]
    for bus in snapshot.buses:
        on_time = abs(bus.deviation_seconds) <= ON_TIME_TOLERANCE_SECONDS
        minutes = (abs(bus.deviation_seconds) + 30) // 60
        state = (
            "BEHIND"
            if bus.deviation_seconds > 0
            else "AHEAD"
            if bus.deviation_seconds < 0
            else "ON TIME"
        )
        if on_time:
            state = "ON TIME"
        if use_color and state != "ON TIME":
            state = f"{RED if state == 'BEHIND' else YELLOW}{state}{RESET}"
        scheduled = bus.scheduled_departure.strftime("%-I:%M %p")
        deviation = "ON TIME" if on_time else f"{minutes} min {state}"
        vehicle = f" · Vehicle {bus.vehicle_id}" if bus.vehicle_id else ""
        bus_direction = (
            f" · {bus.direction_label}" if snapshot.direction_label == "All directions" else ""
        )
        lines.extend(
            (
                f"Bus {bus.run_number} of {bus.run_total}{bus_direction} · scheduled {scheduled}",
                f"{deviation} as of {bus.as_of_stop.name} · "
                f"{_freshness(bus.observed_at, snapshot.fetched_at)}{vehicle}",
            )
        )
    return "\n".join(lines)


def _freshness(observed_at: datetime | None, fetched_at: datetime) -> str:
    if observed_at is None:
        return "updated time unavailable"
    age_seconds = max(0, int((fetched_at - observed_at).total_seconds()))
    if age_seconds < 60:
        return f"updated {age_seconds} sec ago"
    age_minutes = age_seconds // 60
    return f"updated {age_minutes} min ago"


def render_stops(stops: list[tuple[str, str]]) -> str:
    return "\n".join(f"{stop_id:<10} {name}" for stop_id, name in stops)


def clear_screen() -> None:
    print("\033[2J\033[H", end="")
