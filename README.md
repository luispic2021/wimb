# WIMB — Where Is My Bus?

WIMB is a factual terminal display for Golden Gate Transit Route 154 (Novato ↔ San
Francisco). It answers: *which commuter run is coming, and how was it running at
the latest stop supported by realtime evidence?*

It is deliberately **not** an arrival predictor. A displayed deviation is measured
at the vehicle's current or last-passed stop and is always labeled `as of <stop>`.
WIMB never projects that deviation to your selected stop.

## Setup

Requires Python 3.11+ and a free [511 SF Bay Open Data API key](https://511.org/open-data/transit).

```sh
cp wimb.example.toml wimb.toml
make install
```

Create `.env` in the project root:

```dotenv
WIMB_API_KEY=your_511_key
```

Find route-154 stop IDs, then put one in `wimb.toml` or pass it at runtime:

```sh
.venv/bin/wimb --list-stops
.venv/bin/wimb --stop STOP_ID --direction 0 --count 2
```

Run `make run` after configuring `wimb.toml`.

## Usage

```sh
# One factual snapshot
.venv/bin/wimb --stop 40104 --direction 0

# Refresh/redraw every 60 seconds
.venv/bin/wimb --stop 40104 --direction 0 --watch

# Help and version
.venv/bin/wimb --help
.venv/bin/wimb --version
```

Example shape:

```text
Route 154
Direction: Southbound to San Francisco
Stop: Manzanita Park & Ride

Bus 6 of 7 · scheduled 7:12 AM
5 min AHEAD as of Lucas Valley Road · updated 24 sec ago · Vehicle 1204
```

“Bus 6 of 7” is the run's position in that service date and direction's published
timetable, not a physical bus number. WIMB numbers active runs by their first
scheduled trip departure, with trip ID as a deterministic tie-breaker, so the
number remains stable at every selected stop. GTFS calendar exceptions are applied,
and times beyond 24:00 stay associated with their originating service date.

Direction IDs are treated as opaque GTFS values. The human label uses the trip
headsign (or final stop) for its destination and compares the first and last stop
latitudes to derive northbound or southbound. If GTFS lacks enough geographic data,
WIMB honestly renders `To <destination>` instead of guessing from the ID.

`AHEAD` and `BEHIND` are colorized when output is a terminal. Deviations within
±59 seconds are `ON TIME`. Freshness comes from the TripUpdate timestamp when
available, otherwise the VehiclePosition timestamp; a missing timestamp is shown
as `updated time unavailable`.

## Approaching and evidence rules

WIMB joins static and realtime records by exact GTFS trip ID (and realtime service
date when supplied). Vehicle stop sequence and status decide whether that trip is
still approaching the selected stop. A bus in transit to, incoming at, or stopped
at the selected sequence remains visible; progress beyond it removes the bus.

When realtime progress has neither a stop sequence nor a resolvable stop ID, WIMB
uses a bounded schedule fallback: 30 minutes before through 60 minutes after the
selected stop's scheduled time. This fallback affects eligibility only. It never
turns a future StopTimeUpdate into a historical observation.

Deviation evidence is limited to a stop that VehiclePositions prove the bus has
reached: the previous stop while it is in transit/incoming, or the current stop
while it is stopped. A predicted update for a future stop is withheld.

## Data and rate limits

On its first run, WIMB discovers the Golden Gate Transit operator through the 511
operators endpoint, then caches that identity and the static GTFS zip in `.wimb/`.
Static GTFS refreshes weekly. Each normal snapshot requests TripUpdates and
VehiclePositions on demand.

`--watch` intentionally refreshes every 60 seconds, which is two realtime requests
per minute (120/hour). 511's published default token limit is 60/hour, so use watch
only after requesting a quota increase from 511; otherwise use one-off snapshots.

## Errors are explicit

- **No live vehicles on route 154 right now**: VehiclePositions contained no route-154 vehicle.
- **Feed is stale**: WIMB refuses to present old realtime data as live.
- **511 unavailable / rejected key**: HTTP and network failures are reported separately.
- **No approaching bus with a stop-level deviation**: vehicles exist, but none can
  be shown for the selected stop without forecasting.

## Development

```sh
make test
make lint
```

Tests use local GTFS text fixtures and in-memory protobuf fixtures; no tests call
the network.
