# WIMB — Where Is My Bus?

WIMB is a factual terminal display for Golden Gate Transit route 154 (Novato ↔ San
Francisco). It answers: *is my bus ahead or behind schedule, and where is it now?*

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
Route 154 → Novato - San Francisco
Stop: Manzanita Park & Ride

Bus 1204   sched 7:12 AM   4 min BEHIND   as of Larkspur Landing
Bus 1187   sched 7:42 AM   1 min AHEAD    as of Novato
```

`AHEAD` and `BEHIND` are colorized when output is a terminal. Ahead is called out
because an early bus does not wait.

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
- **No current-stop delay fact**: a vehicle exists but cannot be paired with a delay at its present or last-passed stop. WIMB withholds it instead of forecasting.

## Development

```sh
make test
make lint
```

Tests use local GTFS text fixtures and in-memory protobuf fixtures; no tests call
the network.
