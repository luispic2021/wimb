"""Terminal rendering only; it does not compute business facts."""

from __future__ import annotations

import sys

from .models import RouteSnapshot

RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def render_snapshot(snapshot: RouteSnapshot, color: bool | None = None) -> str:
    use_color = sys.stdout.isatty() if color is None else color
    lines = [
        f"Route {snapshot.route_id} → {snapshot.destination}",
        f"Stop: {snapshot.selected_stop.name}",
        "",
    ]
    for bus in snapshot.buses:
        minutes = max(1, round(abs(bus.deviation_seconds) / 60))
        state = (
            "BEHIND"
            if bus.deviation_seconds > 0
            else "AHEAD"
            if bus.deviation_seconds < 0
            else "ON TIME"
        )
        if use_color and state != "ON TIME":
            state = f"{RED if state == 'BEHIND' else YELLOW}{state}{RESET}"
        vehicle = bus.vehicle_id or bus.trip_id
        scheduled = bus.scheduled_departure.strftime("%-I:%M %p")
        deviation = "on time" if bus.deviation_seconds == 0 else f"{minutes} min {state}"
        lines.append(
            f"Bus {vehicle}   sched {scheduled}   {deviation}   as of {bus.as_of_stop.name}"
        )
    return "\n".join(lines)


def render_stops(stops: list[tuple[str, str]]) -> str:
    return "\n".join(f"{stop_id:<10} {name}" for stop_id, name in stops)


def clear_screen() -> None:
    print("\033[2J\033[H", end="")
