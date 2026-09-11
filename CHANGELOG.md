# Changelog

## v0.3.0

### Added

- Direction and stop selections on the web UI now sync to the URL
  (`?direction_id=&stop_id=`) and restore on reload instead of resetting to a
  hardcoded default direction and stop.
- Bus status cards show an estimated arrival time (ETA) when the tracked
  vehicle's schedule deviation supports one.

### Changed

- Visiting the web app with no selection in the URL no longer auto-picks a
  direction or stop; the `/stops` and `/status` APIs are no longer called
  until the user chooses one, avoiding wasted requests for routes/stops
  nobody asked to see.

### Fixed

- A vehicle is no longer treated as still approaching a stop once its
  estimated arrival has clearly passed with no fresh progress evidence to
  support it.
- Reverting a direction or stop selection while its stops/status request was
  still in flight no longer lets the stale response overwrite the UI for the
  selection the user has since cleared.
- A direction that currently publishes no stops no longer leaves a stale
  `stop_id` in the URL.

## v0.2.0

### Added

- FastAPI application with versioned Route 154 routes, directions, stops, and status APIs.
- Responsive browser interface served by the same Python application.
- One-worker, 60-second realtime feed cache with concurrent refresh deduplication.
- Structured HTTP error responses for validation, uncertainty, stale data, and 511 failures.

## v0.1.0

### Added

- Initial WIMB schedule-deviation CLI for Golden Gate Transit route 154.
