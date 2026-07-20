# WIMB project guidance

## Purpose and scope

WIMB (“Where Is My Bus?”) helps Golden Gate Transit Route 154 commuters identify
which published commuter run is coming and see its factual schedule deviation at
the latest stop supported by realtime evidence. The MVP is Route 154 only.

“Bus X of 7” means the run's 1-based position in the active service date and
direction's published timetable. It is not the physical vehicle or fleet ID.
Vehicle ID is secondary diagnostic information.

WIMB is not an arrival predictor. Keep every deviation attached to its evidence
stop and render `as of <stop>`. Never project that deviation to the user's selected
stop or present a predicted future StopTimeUpdate as a completed observation.

## Architecture and project structure

This is a modular Python monolith and terminal application:

- `src/wimb/client.py`: read-only 511 HTTP and protobuf transport.
- `src/wimb/gtfs.py`: static GTFS caching, parsing, service calendars, run identity,
  stop lookup, and direction derivation.
- `src/wimb/realtime.py`: GTFS-Realtime protobuf-to-domain translation.
- `src/wimb/deviation.py`: realtime progress eligibility and factual evidence join.
- `src/wimb/service.py`: Route 154 application orchestration and feed freshness.
- `src/wimb/presentation.py`: terminal rendering only.
- `src/wimb/config.py` and `cli.py`: local configuration and CLI wiring.
- `scripts/audit_route_154.py`: cron-invoked Route 154 CLI audit capture; it records
  validation evidence and is not application operational logging.
- `tests/fixtures`: offline GTFS text fixtures; protobuf fixtures are built in memory.

Keep domain and presentation logic separate. Prefer focused additions over broad
repository restructuring. The intended deployment path is a FastAPI layer later,
with the modular monolith on one DigitalOcean droplet; neither is part of the CLI
MVP.

## GTFS and realtime semantics

- Apply both `calendar.txt` and `calendar_dates.txt` to the GTFS service date.
- GTFS times may exceed 24:00 and remain attached to the originating service date.
- Number runs per service date and direction using the trip's first scheduled
  departure as the stable anchor and trip ID as the deterministic tie-breaker.
  Never derive run identity from the selected stop.
- Do not assume what `direction_id` 0 or 1 means. Use trip destination data and
  endpoint latitudes; fall back to `To <destination>` if compass direction cannot
  be established.
- Match static and realtime trips by exact `trip_id`, and use realtime `start_date`
  when available to disambiguate service-day instances.
- VehiclePosition `current_stop_sequence`, `stop_id`, and `current_status` establish
  progress. If progress is absent, eligibility may use only the documented bounded
  schedule fallback in `deviation.py`.
- TripUpdates commonly include predictions. Only use an update at a stop that
  vehicle progress proves has been reached or passed. Withhold unsupported claims.
- Feed header timestamps gate overall staleness. Per-bus freshness prefers the
  TripUpdate timestamp and falls back to VehiclePosition; disclose missing time.
- Deviations within the named ±59-second tolerance render as `ON TIME`.

## Setup and commands

Requires Python 3.11+.

```sh
cp wimb.example.toml wimb.toml
make install
make test
make lint
make run
```

Run a one-shot snapshot with `.venv/bin/wimb --stop STOP_ID --direction ID` and
discover Route 154 stops with `.venv/bin/wimb --list-stops`.

## Configuration, security, and conventions

Keep the 511 key in `WIMB_API_KEY` in the shell or an untracked `.env`; never print,
commit, log, or embed it in URLs shown to users. Cached public GTFS data belongs in
`.wimb/`. Live validation must be read-only and one-shot unless the API quota is
known to support polling.

The audit collector writes text and JSONL records under `/var/log/wimb` by default.
It must invoke the existing CLI rather than duplicate domain logic, and neither
audit stream may contain secrets from the environment or `.env`.

Keep tests deterministic and offline. Add local GTFS fixtures or in-memory
protobufs for schedule/realtime cases. Run `make test` for the full offline suite,
then `make lint` for Ruff and strict mypy before
committing. Preserve explicit user-facing failures when live data is absent,
stale, or insufficient.

## Explicitly out of scope

Do not add other routes, FastAPI, a web UI, deployment automation, accounts,
subscriptions, databases, historical tracking, maps, notifications, machine
learning, or broad architectural restructuring during the Route 154 CLI MVP.
