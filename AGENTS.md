# WIMB project guidance

## Purpose and scope

WIMB (“Where Is My Bus?”) helps Golden Gate Transit Route 154 commuters identify
which published commuter run is coming and see a transparent arrival estimate based
on the latest stop supported by realtime evidence. The MVP is Route 154 only.

“Bus X of 7” means the run's 1-based position in the active service date and
direction's published timetable. It is not the physical vehicle or fleet ID.
Vehicle ID is secondary diagnostic information.

Compute arrival as the selected stop's scheduled time plus the deviation at the
latest confirmed evidence stop, minus the snapshot time. Keep every estimate
attached to its evidence stop and render `as of <stop>`. Never present a predicted
future StopTimeUpdate as a completed observation.

## Architecture and project structure

This is a modular Python monolith with terminal and FastAPI delivery layers:

- `src/wimb/client.py`: read-only 511 HTTP and protobuf transport.
- `src/wimb/gtfs.py`: static GTFS caching, parsing, service calendars, run identity,
  stop lookup, and direction derivation.
- `src/wimb/realtime.py`: GTFS-Realtime protobuf-to-domain translation.
- `src/wimb/deviation.py`: realtime progress eligibility and factual evidence join.
- `src/wimb/progress.py`: locked, atomic latest-progress checkpoint used to reject
  regressing stop sequences across separate CLI and cron executions.
- `src/wimb/service.py`: Route 154 application orchestration and feed freshness.
- `src/wimb/presentation.py`: terminal rendering only.
- `src/wimb/config.py` and `cli.py`: local configuration and CLI wiring.
- `src/wimb/realtime_cache.py`: one-process paired realtime feed cache and concurrent
  refresh deduplication for web requests.
- `src/wimb/web/app.py` and `schemas.py`: FastAPI entry point, versioned `/api/v1`
  routes, explicit response schemas, and HTTP error mapping.
- `src/wimb/web/static/`: plain HTML, CSS, and JavaScript for the responsive UI;
  browser data access goes only through `/api/v1`.
- `scripts/audit_route_154.py`: cron-invoked Route 154 CLI audit capture; it records
  validation evidence and is not application operational logging.
- `tests/fixtures`: offline GTFS text fixtures; protobuf fixtures are built in memory.

Keep domain and presentation logic separate. The CLI and FastAPI layer must call
the same application and domain services; never duplicate GTFS matching, run
numbering, eligibility, or deviation logic in routes or browser code. Prefer
focused additions over broad repository restructuring. The intended production
shape is one DigitalOcean droplet with Caddy/HTTPS in front of a localhost-bound
Uvicorn process.

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
- Arrival countdowns preserve whole-second precision without rounding.
- `--count` limits timetable candidates, not only fully tracked facts. Never invent
  missing vehicles or evidence; render explicit timetable-only states instead.
- Retain only the furthest confirmed evidence per service-date/trip in
  `.wimb/route-progress.json`. This expiring correctness cache is not a vehicle
  history; never infer a stop when both current evidence and the cache are absent.

## Setup and commands

Requires Python 3.11+.

```sh
cp wimb.example.toml wimb.toml
make install
make test
make lint
make run
make web
```

Run a one-shot snapshot with `.venv/bin/wimb --stop STOP_ID --direction ID` and
discover Route 154 stops with `.venv/bin/wimb --list-stops`.
Run the local web application with `make web`; its OpenAPI documentation is at
`/docs`.

## Configuration, security, and conventions

Keep the 511 key in `WIMB_API_KEY` in the shell or an untracked `.env`; never print,
commit, log, or embed it in URLs shown to users. Cached public GTFS and the minimal
latest-progress checkpoint belong in `.wimb/`. Network access remains read-only;
poll only when the API quota is known to support it.

Browsers must never call 511 directly or receive its credential. Web status
requests use the centralized process-local realtime cache, which stores paired
TripUpdates and VehiclePositions for approximately 60 seconds and deduplicates
concurrent refreshes. Run one Uvicorn worker; multiple workers require a shared
cache in a later architecture.

The audit collector writes text and JSONL records under `/var/log/wimb` by default.
It must invoke the existing CLI rather than duplicate domain logic, and neither
audit stream may contain secrets from the environment or `.env`.

Keep tests deterministic and offline. Add local GTFS fixtures or in-memory
protobufs for schedule/realtime cases. Run `make test` for the full offline suite,
then `make lint` for Ruff and strict mypy before
committing. Preserve explicit user-facing failures when live data is absent,
stale, or insufficient.

Version API routes under `/api/v1` and use explicit Pydantic response schemas.
Map invalid resources, no-service and uncertainty states, stale feeds, upstream
authentication, temporary upstream failure, and unexpected errors intentionally;
never expose secrets or raw internal exceptions in HTTP responses or logs.

## Explicitly out of scope

Do not add other transit routes, deployment automation, Caddy configuration,
containers, multiple Uvicorn workers, shared caches such as Redis, accounts,
subscriptions, databases, historical tracking beyond the expiring latest-progress
checkpoint, maps, notifications, machine learning, frontend frameworks, or broad
architectural restructuring.
