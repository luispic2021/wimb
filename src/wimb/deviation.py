"""Pure schedule-deviation computation; deliberately contains no forecasts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .gtfs import GtfsStore
from .models import (
    BusFact,
    RealtimeStopUpdate,
    ScheduledRun,
    Stop,
    StopStatus,
    TripUpdate,
    VehiclePosition,
)

SCHEDULE_FALLBACK_EARLY = timedelta(minutes=30)
SCHEDULE_FALLBACK_LATE = timedelta(minutes=60)


def build_bus_facts(
    gtfs: GtfsStore,
    scheduled_runs: Iterable[ScheduledRun],
    updates: Iterable[TripUpdate],
    positions: Iterable[VehiclePosition],
    now: datetime,
) -> list[BusFact]:
    """Join scheduled runs to progress-confirmed, current-stop realtime evidence.

    A fact is emitted only when a vehicle position and a delay associated with its
    stopped-at or last-passed stop are both present. Future stop predictions are
    never used as historical observations.
    """
    updates_by_trip = {update.trip_id: update for update in updates if update.trip_id}
    positions_by_trip = {position.trip_id: position for position in positions if position.trip_id}
    runs_by_trip: dict[str, list[ScheduledRun]] = {}
    for run in scheduled_runs:
        runs_by_trip.setdefault(run.stop_time.trip_id, []).append(run)
    facts: list[BusFact] = []
    for trip_id, candidate_runs in runs_by_trip.items():
        update = updates_by_trip.get(trip_id)
        position = positions_by_trip.get(trip_id)
        if update is None or position is None:
            continue
        selected_run = _matching_service_run(candidate_runs, update, position, now)
        if selected_run is None:
            continue
        if not is_still_approaching(gtfs, selected_run, position, now):
            continue
        evidence = _current_stop_evidence(gtfs, trip_id, update, position)
        if evidence is None:
            continue
        stop, delay_seconds = evidence
        facts.append(
            BusFact(
                trip_id,
                selected_run.run_number,
                selected_run.run_total,
                selected_run.direction_label,
                position.vehicle_id or update.vehicle_id,
                selected_run.scheduled_departure,
                delay_seconds,
                stop,
                update.timestamp or position.timestamp,
            )
        )
    return sorted(facts, key=lambda fact: fact.scheduled_departure)


def _matching_service_run(
    runs: list[ScheduledRun], update: TripUpdate, position: VehiclePosition, now: datetime
) -> ScheduledRun | None:
    service_date = position.service_date or update.service_date
    if service_date is not None:
        return next((run for run in runs if run.service_date == service_date), None)
    if not runs:
        return None
    return min(runs, key=lambda run: abs(run.scheduled_departure - now))


def is_still_approaching(
    gtfs: GtfsStore, run: ScheduledRun, position: VehiclePosition, now: datetime
) -> bool:
    """Use vehicle progress first, then a deliberately bounded schedule fallback."""
    progress_sequence = position.current_stop_sequence
    if progress_sequence is None and position.stop_id:
        progress_sequence = gtfs.stop_sequence(run.stop_time.trip_id, position.stop_id)
    if progress_sequence is not None:
        return progress_sequence <= run.stop_time.stop_sequence
    return (
        run.scheduled_departure - SCHEDULE_FALLBACK_EARLY
        <= now
        <= run.scheduled_departure + SCHEDULE_FALLBACK_LATE
    )


def _current_stop_evidence(
    gtfs: GtfsStore,
    trip_id: str,
    update: TripUpdate,
    position: VehiclePosition,
) -> tuple[Stop, int] | None:
    current_sequence = position.current_stop_sequence
    if current_sequence is None and position.stop_id:
        current_sequence = gtfs.stop_sequence(trip_id, position.stop_id)
    if position.status in (StopStatus.IN_TRANSIT_TO, StopStatus.INCOMING_AT) and (current_sequence):
        stop = gtfs.previous_stop(trip_id, current_sequence)
        update_match = _last_update_before(update.stop_updates, current_sequence)
    elif position.status is StopStatus.STOPPED_AT:
        stop = gtfs.stops.get(position.stop_id) if position.stop_id else None
        if stop is None and current_sequence:
            stop = gtfs.stop_at_sequence(trip_id, current_sequence)
        update_match = _matching_update(update.stop_updates, position.stop_id, current_sequence)
    else:
        return None
    if stop is None or update_match is None or update_match.delay_seconds is None:
        return None
    return stop, update_match.delay_seconds


def _matching_update(
    updates: Iterable[RealtimeStopUpdate], stop_id: str | None, sequence: int | None
) -> RealtimeStopUpdate | None:
    for update in updates:
        if sequence is not None and update.stop_sequence == sequence:
            return update
        if stop_id is not None and update.stop_id == stop_id:
            return update
    return None


def _last_update_before(
    updates: Iterable[RealtimeStopUpdate], sequence: int
) -> RealtimeStopUpdate | None:
    candidates = [
        update
        for update in updates
        if update.stop_sequence is not None and update.stop_sequence < sequence
    ]
    return max(candidates, key=lambda update: update.stop_sequence or -1) if candidates else None
