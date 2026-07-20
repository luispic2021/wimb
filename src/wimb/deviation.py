"""Schedule-deviation evidence and honest timetable candidate selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta

from .gtfs import GtfsStore
from .models import (
    BusFact,
    ProgressEvidence,
    RealtimeStopUpdate,
    ScheduledRun,
    StopStatus,
    TrackingStatus,
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
    prior_progress: Mapping[tuple[date, str], ProgressEvidence] | None = None,
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
        if position is None:
            continue
        if update is None:
            update = TripUpdate(trip_id, None, position.vehicle_id, ())
        selected_run = _matching_service_run(candidate_runs, update, position, now)
        if selected_run is None:
            continue
        prior = (prior_progress or {}).get((selected_run.service_date, trip_id))
        if not is_still_approaching(
            gtfs,
            selected_run,
            position,
            now,
            prior.stop_sequence if prior else None,
        ):
            continue
        current_evidence = _current_stop_evidence(gtfs, trip_id, update, position)
        evidence = _furthest_evidence(current_evidence, prior)
        if evidence is None:
            continue
        facts.append(
            BusFact(
                trip_id,
                selected_run.service_date,
                selected_run.run_number,
                selected_run.run_total,
                selected_run.direction_label,
                position.vehicle_id or update.vehicle_id,
                selected_run.scheduled_departure,
                evidence.delay_seconds,
                evidence.stop,
                evidence.stop_sequence,
                evidence.observed_at,
            )
        )
    return sorted(facts, key=lambda fact: fact.scheduled_departure)


def build_bus_listing(
    gtfs: GtfsStore,
    scheduled_runs: Iterable[ScheduledRun],
    updates: Iterable[TripUpdate],
    positions: Iterable[VehiclePosition],
    now: datetime,
    count: int,
    prior_progress: Mapping[tuple[date, str], ProgressEvidence] | None = None,
) -> tuple[list[BusFact], bool]:
    """Combine tracked facts with honest timetable-only candidates."""
    runs = [run for run in scheduled_runs if run.scheduled_departure.date() == now.date()]
    update_items = list(updates)
    position_items = list(positions)
    tracked = build_bus_facts(gtfs, runs, update_items, position_items, now, prior_progress)
    tracked_by_run = {(fact.trip_id, fact.scheduled_departure): fact for fact in tracked}
    positions_by_trip = {
        position.trip_id: position for position in position_items if position.trip_id
    }
    candidates: list[BusFact] = []
    for run in runs:
        key = (run.stop_time.trip_id, run.scheduled_departure)
        fact = tracked_by_run.get(key)
        if fact is not None:
            candidates.append(fact)
            continue

        position = positions_by_trip.get(run.stop_time.trip_id)
        if position is not None:
            matching_run = _matching_service_run(
                [run], _update_for_trip(update_items, run), position, now
            )
            prior = (prior_progress or {}).get((run.service_date, run.stop_time.trip_id))
            if matching_run is None or not is_still_approaching(
                gtfs,
                run,
                position,
                now,
                prior.stop_sequence if prior else None,
            ):
                continue
            status = TrackingStatus.UNAVAILABLE
        elif run.scheduled_start > now:
            status = TrackingStatus.NOT_DEPARTED
        elif run.scheduled_departure >= now:
            status = TrackingStatus.UNAVAILABLE
        else:
            continue

        candidates.append(
            BusFact(
                trip_id=run.stop_time.trip_id,
                service_date=run.service_date,
                run_number=run.run_number,
                run_total=run.run_total,
                direction_label=run.direction_label,
                vehicle_id=position.vehicle_id if position else None,
                scheduled_departure=run.scheduled_departure,
                deviation_seconds=None,
                as_of_stop=None,
                as_of_stop_sequence=None,
                observed_at=position.timestamp if position else None,
                tracking_status=status,
            )
        )

    candidates.sort(key=lambda bus: bus.scheduled_departure)
    return candidates[:count], len(candidates) < count


def _update_for_trip(updates: Iterable[TripUpdate], run: ScheduledRun) -> TripUpdate:
    return next(
        (
            update
            for update in updates
            if update.trip_id == run.stop_time.trip_id
            and (update.service_date is None or update.service_date == run.service_date)
        ),
        TripUpdate(run.stop_time.trip_id, None, None, ()),
    )


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
    gtfs: GtfsStore,
    run: ScheduledRun,
    position: VehiclePosition,
    now: datetime,
    confirmed_stop_sequence: int | None = None,
) -> bool:
    """Use vehicle progress first, then a deliberately bounded schedule fallback."""
    progress_sequence = position.current_stop_sequence
    if progress_sequence is None and position.stop_id:
        progress_sequence = gtfs.stop_sequence(run.stop_time.trip_id, position.stop_id)
    if confirmed_stop_sequence is not None:
        checkpoint_progress = confirmed_stop_sequence
        if position.status is not StopStatus.STOPPED_AT:
            checkpoint_progress += 1
        progress_sequence = max(progress_sequence or 0, checkpoint_progress)
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
) -> ProgressEvidence | None:
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
    if update_match.stop_sequence is None:
        return None
    return ProgressEvidence(
        trip_id=trip_id,
        service_date=position.service_date or update.service_date or date.min,
        stop_sequence=update_match.stop_sequence,
        stop=stop,
        delay_seconds=update_match.delay_seconds,
        observed_at=update.timestamp or position.timestamp,
    )


def _furthest_evidence(
    current: ProgressEvidence | None, prior: ProgressEvidence | None
) -> ProgressEvidence | None:
    if current is None:
        return prior
    if prior is None or current.stop_sequence > prior.stop_sequence:
        return current
    if current.stop_sequence < prior.stop_sequence:
        return prior
    if current.observed_at is None:
        return prior
    if prior.observed_at is None or current.observed_at >= prior.observed_at:
        return current
    return prior


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
