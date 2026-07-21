"""Terminal rendering only; it does not compute business facts."""

from __future__ import annotations

from datetime import datetime

from .models import RouteSnapshot, TrackingStatus


def render_snapshot(snapshot: RouteSnapshot, color: bool | None = None) -> str:
    lines = [
        f"Route {snapshot.route_id}",
        f"Direction: {snapshot.direction_label}",
        f"Stop: {snapshot.selected_stop.name}",
        "",
    ]
    for bus in snapshot.buses:
        scheduled = bus.scheduled_departure.strftime("%-I:%M %p")
        vehicle = f" · Vehicle {bus.vehicle_id}" if bus.vehicle_id else ""
        bus_direction = (
            f" · {bus.direction_label}" if snapshot.direction_label == "All directions" else ""
        )
        lines.append(
            f"Bus {bus.run_number} of {bus.run_total}{bus_direction} · "
            f"scheduled {scheduled}{vehicle}"
        )
        if bus.tracking_status is TrackingStatus.TRACKED:
            assert bus.as_of_stop is not None
            assert bus.estimated_arrival is not None
            seconds_until = int((bus.estimated_arrival - snapshot.fetched_at).total_seconds())
            if seconds_until >= 0:
                arrival = f"Arrives in: {_duration(seconds_until)}"
            else:
                arrival = f"Arrival estimate overdue by: {_duration(abs(seconds_until))}"
            eta = bus.estimated_arrival.strftime("%-I:%M %p")
            lines.append(f"ETA: {eta} · {arrival}")
            lines.append(
                f"{_deviation(bus.deviation_seconds)} as of {bus.as_of_stop.name} · "
                f"{_freshness(bus.observed_at, snapshot.fetched_at)}"
            )
        elif bus.tracking_status is TrackingStatus.NOT_DEPARTED:
            lines.append("Timetable only: this bus has not departed yet.")
        else:
            lines.append("Timetable only: live tracking is currently unavailable.")
        lines.append("")
    if snapshot.no_additional_buses:
        lines.append("No additional buses are scheduled in this direction today.")
    return "\n".join(lines).rstrip()


def _duration(total_seconds: int) -> str:
    if total_seconds < 60:
        return f"{total_seconds:02d} seconds"
    minutes, seconds = divmod(total_seconds, 60)
    minute_unit = "minute" if minutes == 1 else "minutes"
    return f"{minutes} {minute_unit} and {seconds:02d} seconds"


def _deviation(seconds: int | None) -> str:
    assert seconds is not None
    if abs(seconds) <= 59:
        return "ON TIME"
    state = "behind" if seconds > 0 else "ahead"
    return f"{_duration(abs(seconds))} {state}"


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
