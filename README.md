# WIMB — Where Is My Bus?

WIMB is a terminal and web application for Golden Gate Transit Route 154 (Novato ↔
San Francisco). It answers: *which commuter run is coming, and when should it reach
my stop based on its latest confirmed schedule deviation?*

The arrival estimate is deliberately simple and auditable: the selected stop's
scheduled time plus the deviation measured at the latest stop supported by realtime
evidence. It is always labeled `as of <stop>` and does not use an unsupported future
StopTimeUpdate as though the bus had already reached that stop.

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

## Local web application

Start the FastAPI/Uvicorn application with one worker:

```sh
make web
# Equivalent:
.venv/bin/uvicorn wimb.web.app:app --reload --host 127.0.0.1 --port 8000
```

Open the responsive Route 154 interface at <http://127.0.0.1:8000/> and the
interactive OpenAPI documentation at <http://127.0.0.1:8000/docs>. The UI is plain
HTML, CSS, and JavaScript served by FastAPI. It reads directions, ordered stops,
and transit status only through the versioned API; the browser never contacts 511
or receives the API key.

Useful API requests:

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
curl http://127.0.0.1:8000/api/v1/routes
curl http://127.0.0.1:8000/api/v1/routes/154/directions
curl 'http://127.0.0.1:8000/api/v1/routes/154/stops?direction_id=1'
curl 'http://127.0.0.1:8000/api/v1/routes/154/status?direction_id=1&stop_id=40581'
```

`/health` confirms only that the process is alive. `/ready` checks required local
configuration and cache-directory access without spending 511 quota. API responses
use timezone-aware ISO 8601 timestamps and explicit data states such as `live`,
`no_service`, `no_live_vehicles`, and `no_usable_realtime_data`. Invalid selections,
stale feeds, rejected server credentials, temporary 511 failures, and unexpected
errors have distinct status codes and stable error codes.

### Web cache and production direction

The web runtime caches TripUpdates and VehiclePositions together in memory for 60
seconds. Requests inside that window reuse the same pair of feeds, and concurrent
requests share one refresh instead of issuing simultaneous 511 calls. Static GTFS
continues to use the persistent `.wimb/` filesystem cache. A failed realtime
refresh is shared for a short five-second cooldown so an outage cannot cause queued
requests to retry 511 in a burst.

The in-memory realtime cache is intentionally designed for one Uvicorn worker. A
future multi-worker deployment will require a shared cache, but Redis and multiple
workers are outside this version. The intended production topology is Caddy/HTTPS
in front of a Uvicorn process bound to localhost on the same DigitalOcean droplet;
the external port, Caddy configuration, DNS, and droplet provisioning are not
assumed by the application.

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

Bus 6 of 7 · scheduled 7:57 AM · Vehicle 964
Arrives in: 6 minutes and 07 seconds as of Terra Linda Bus Pad · updated 14 sec ago

Bus 7 of 7 · scheduled 8:27 AM
Timetable only: this bus has not departed yet.
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

Countdowns retain whole-second precision without rounding. Values below one minute
render as seconds; longer values render as minutes plus two-digit seconds. A
still-approaching bus whose estimate is already in the past is labeled `Arrival
estimate overdue by` instead of showing a negative countdown. Freshness comes from
the TripUpdate timestamp when available, otherwise the VehiclePosition timestamp;
a missing timestamp is shown as `updated time unavailable`.

`--count` is the maximum number of timetable candidates to display. WIMB walks the
remaining timetable in order, showing live evidence where available and an honest
timetable-only candidate where it is not. It does not invent vehicles, last-seen
stops, or deviations. A future run is labeled as not departed; a started run without
usable progress is labeled `live tracking is currently unavailable`. If fewer
candidates remain, WIMB states that no additional buses are scheduled in that
direction today.

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
while it is stopped. A predicted update for a future stop is withheld. WIMB carries
that confirmed deviation forward to the selected stop only for the transparent
arrival calculation documented above.

Because 511 can temporarily report a lower or missing vehicle stop sequence, WIMB
keeps the furthest confirmed stop and its deviation for each service-date/trip pair
in `.wimb/route-progress.json`. The file is updated under a lock with atomic
replacement and entries expire after two days. It stores only the latest checkpoint,
not a vehicle-location history. Separate CLI and cron executions therefore cannot
move a bus backward from Terra Linda to Lucas Valley; when neither the current feed
nor the checkpoint supports progress, WIMB shows timetable-only information.

## Data and rate limits

On its first run, WIMB discovers the Golden Gate Transit operator through the 511
operators endpoint, then caches that identity and the static GTFS zip in `.wimb/`.
Static GTFS refreshes weekly. Each normal snapshot requests TripUpdates and
VehiclePositions on demand and refreshes the small latest-progress checkpoint when
confirmed evidence advances.

`--watch` intentionally refreshes every 60 seconds, which is two realtime requests
per minute (120/hour). 511's published default token limit is 60/hour, so use watch
only after requesting a quota increase from 511; otherwise use one-off snapshots.

## Errors are explicit

- **Feed is stale**: WIMB refuses to present old realtime data as live.
- **511 unavailable / rejected key**: HTTP and network failures are reported separately.
- **Timetable only**: the run is scheduled but WIMB cannot support a live arrival
  estimate without fabricating realtime evidence.
- **No additional buses**: no more timetable candidates remain for the selected
  direction on the current calendar day.

## Development

```sh
make test
make lint
```

Tests use local GTFS text fixtures and in-memory protobuf fixtures; no tests call
the network.

## Route 154 audit collector

[`scripts/audit_route_154.py`](scripts/audit_route_154.py) is a cron-oriented audit
utility for the AM southbound and PM northbound commuter experiences. It invokes the
installed WIMB CLI once and captures exactly what the CLI returned; it does not
duplicate or parse WIMB's timetable, run-numbering, eligibility, deviation, or
rendering logic. These are validation records, not application operational logs.

The defaults are Route 154, southbound `direction_id=1`, stop `40581`, and two
buses. `--commute am` selects those same values; `--commute pm` selects northbound
`direction_id=0` at stop `40057`. Explicit `--stop`, `--direction`, and
`--direction-label` values override a preset. Run from the installed repository
environment so `.venv/bin/wimb` and the repository `.env` are available:

```sh
cd /opt/wimb
/opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute am
/opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute pm
```

The default outputs are:

- `/var/log/wimb/route-154-40581-audit.log` — human-readable execution entries
- `/var/log/wimb/route-154-40581-audit.jsonl` — one JSON object per execution
- `/var/log/wimb/route-154-40581-audit.lock` — nonblocking overlap lock

The PM preset uses the corresponding `route-154-40057-audit` filenames.

Use temporary paths for development or override the audited stop, direction,
labels, bus count, or timeout:

```sh
.venv/bin/python scripts/audit_route_154.py --log-dir /tmp/wimb-audit
.venv/bin/python scripts/audit_route_154.py \
  --stop 40581 --stop-name "North San Pedro Road Bus Pad" \
  --direction 1 --direction-label Southbound --count 2 \
  --log-dir /tmp/wimb-audit --timeout 150
```

The 150-second default timeout accounts for the 511 client's 30-second request
timeout and a cold snapshot's possible sequential operator, static GTFS,
TripUpdates, and VehiclePositions requests. A second collector exits successfully
with a skip message if the nonblocking lock is held; it never waits for the first.

After successfully appending both logs, the collector returns WIMB's exit code.
Timeout returns `124`; collector failures such as log permission or execution
errors return `70`. A lock-contention skip returns `0`. WIMB errors, no-service
responses, stale feeds, and empty output are still recorded. Known secret values
from the process environment and `/opt/wimb/.env` are redacted before writing.

Inspect recent records with:

```sh
tail -n 80 /var/log/wimb/route-154-40581-audit.log
tail -n 1 /var/log/wimb/route-154-40581-audit.jsonl | \
  /opt/wimb/.venv/bin/python -m json.tool
```

### Droplet installation and cron

From the account that will own the cron entry and can read `/opt/wimb/.env`:

```sh
cd /opt/wimb
git pull --ff-only
/opt/wimb/.venv/bin/python -m pip install -e /opt/wimb
sudo install -d -m 0750 -o "$(id -un)" -g "$(id -gn)" /var/log/wimb
/opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute am
```

Then add these entries with `crontab -e`:

```cron
# --- Morning commute: stop 40581 southbound, every 5 min weekdays 5:35–9:00 ---
35-55/5 5 * * 1-5 cd /opt/wimb && flock -n /var/lock/wimb-154-am.lock /opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute am >> /var/log/wimb/route-154-am-cron.log 2>&1
*/5     6-8 * * 1-5 cd /opt/wimb && flock -n /var/lock/wimb-154-am.lock /opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute am >> /var/log/wimb/route-154-am-cron.log 2>&1
0       9 * * 1-5 cd /opt/wimb && flock -n /var/lock/wimb-154-am.lock /opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute am >> /var/log/wimb/route-154-am-cron.log 2>&1

# --- Afternoon commute: stop 40057 northbound, every 5 min weekdays 15:00–17:50 ---
*/5    15-16 * * 1-5 cd /opt/wimb && flock -n /var/lock/wimb-154-pm.lock /opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute pm >> /var/log/wimb/route-154-pm-cron.log 2>&1
0-50/5 17 * * 1-5 cd /opt/wimb && flock -n /var/lock/wimb-154-pm.lock /opt/wimb/.venv/bin/python /opt/wimb/scripts/audit_route_154.py --commute pm >> /var/log/wimb/route-154-pm-cron.log 2>&1
```

The separate `route-154-am-cron.log` and `route-154-pm-cron.log` files receive only
unexpected collector-level messages such as lock skips and permission failures.
Normal WIMB stdout and stderr are stored in the audit files. Verify the Droplet
timezone with `timedatectl`; the collector timestamps records explicitly with
`America/Los_Angeles` regardless of the cron daemon's timezone configuration.
