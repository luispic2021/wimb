"""Pure schedule-deviation computation; deliberately contains no forecasts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from .gtfs import GtfsStore
from .models import (
    BusFact,
    RealtimeStopUpdate,
    ScheduledStopTime,
    Stop,
    StopStatus,
    TripUpdate,
    VehiclePosition,
)


def build_bus_facts(
    gtfs: GtfsStore,
    upcoming: Iterable[tuple[ScheduledStopTime, datetime]],
    updates: Iterable[TripUpdate],
    positions: Iterable[VehiclePosition],
) -> list[BusFact]:
    """Join upcoming scheduled trips to current-stop realtime evidence.

    A fact is emitted only when a vehicle position and a delay associated with its
    current (or last-passed) stop are both present. That preserves WIMB's strict
    "as of" semantics and prevents target-stop predictions.
    """
    updates_by_trip = {update.trip_id: update for update in updates if update.trip_id}
    positions_by_trip = {position.trip_id: position for position in positions if position.trip_id}
    facts: list[BusFact] = []
    for scheduled, departure in upcoming:
        trip_id = scheduled.trip_id
        update = updates_by_trip.get(trip_id)
        position = positions_by_trip.get(trip_id)
        if update is None or position is None:
            continue
        evidence = _current_stop_evidence(gtfs, trip_id, update, position)
        if evidence is None:
            continue
        stop, delay_seconds = evidence
        facts.append(
            BusFact(
                trip_id,
                position.vehicle_id or update.vehicle_id,
                departure,
                delay_seconds,
                stop,
                position.timestamp,
            )
        )
    return sorted(facts, key=lambda fact: fact.scheduled_departure)


def _current_stop_evidence(
    gtfs: GtfsStore,
    trip_id: str,
    update: TripUpdate,
    position: VehiclePosition,
) -> tuple[Stop, int] | None:
    if position.status is StopStatus.IN_TRANSIT_TO and position.current_stop_sequence:
        stop = gtfs.previous_stop(trip_id, position.current_stop_sequence)
        update_match = _last_update_before(update.stop_updates, position.current_stop_sequence)
    else:
        stop = gtfs.stops.get(position.stop_id) if position.stop_id else None
        if stop is None and position.current_stop_sequence:
            stop = gtfs.stop_at_sequence(trip_id, position.current_stop_sequence)
        update_match = _matching_update(
            update.stop_updates, position.stop_id, position.current_stop_sequence
        )
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
