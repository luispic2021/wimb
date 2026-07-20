#!/usr/bin/env python3
"""Append one exact WIMB CLI snapshot to Route 154 audit logs."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo

PACIFIC_NAME = "America/Los_Angeles"
PACIFIC = ZoneInfo(PACIFIC_NAME)
ROUTE_ID = "154"
DEFAULT_STOP_ID = "40581"
DEFAULT_STOP_NAME = "North San Pedro Road Bus Pad"
DEFAULT_DIRECTION_ID = 1
DEFAULT_DIRECTION_LABEL = "Southbound"
DEFAULT_BUS_COUNT = 2
DEFAULT_LOG_DIR = Path("/var/log/wimb")
# TransitClient permits 30 seconds per request. A cold snapshot can make four
# sequential requests, so 150 seconds leaves modest overhead without approaching
# the collector's shortest 5-minute cron interval.
DEFAULT_TIMEOUT_SECONDS = 150.0
COLLECTOR_FAILURE_EXIT = 70
TIMEOUT_EXIT = 124
SECRET_NAME_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


@dataclass(frozen=True)
class CommutePreset:
    stop_id: str
    direction_id: int
    direction_label: str


COMMUTE_PRESETS: Mapping[str, CommutePreset] = {
    "am": CommutePreset("40581", 1, "Southbound"),
    "pm": CommutePreset("40057", 0, "Northbound"),
}


@dataclass(frozen=True)
class AuditConfig:
    repository_root: Path
    stop_id: str
    stop_name: str
    direction_id: int
    direction_label: str
    requested_bus_count: int
    log_dir: Path
    timeout_seconds: float

    @property
    def log_stem(self) -> str:
        return f"route-{ROUTE_ID}-{self.stop_id}-audit"


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def execute_wimb(
    config: AuditConfig, clock: Callable[[], float] = time.monotonic
) -> ExecutionResult:
    command = [
        str(config.repository_root / ".venv" / "bin" / "wimb"),
        "--stop",
        config.stop_id,
        "--direction",
        str(config.direction_id),
        "--count",
        str(config.requested_bus_count),
    ]
    started = clock()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed local executable and validated arguments
            command,
            cwd=config.repository_root,
            capture_output=True,
            text=True,
            timeout=config.timeout_seconds,
            check=False,
        )
        return ExecutionResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
            max(0.0, clock() - started),
        )
    except subprocess.TimeoutExpired as error:
        stdout = _timeout_text(error.stdout)
        stderr = _timeout_text(error.stderr)
        message = f"WIMB audit timed out after {config.timeout_seconds:g} seconds."
        stderr = f"{stderr}{'' if not stderr or stderr.endswith(chr(10)) else chr(10)}{message}\n"
        return ExecutionResult(TIMEOUT_EXIT, stdout, stderr, max(0.0, clock() - started))
    except OSError as error:
        return ExecutionResult(
            COLLECTOR_FAILURE_EXIT,
            "",
            f"WIMB collector could not execute the CLI: {error}\n",
            max(0.0, clock() - started),
        )


def run_audit(
    config: AuditConfig,
    *,
    execute: Callable[[AuditConfig], ExecutionResult] = execute_wimb,
    now_provider: Callable[[], datetime] | None = None,
    environ: Mapping[str, str] | None = None,
    git_sha_provider: Callable[[Path], str | None] | None = None,
    version_provider: Callable[[], str | None] | None = None,
) -> int:
    """Run and record one audit while holding a nonblocking process lock."""
    try:
        config.log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return _collector_error(f"cannot create log directory {config.log_dir}: {error}")

    lock_path = config.log_dir / f"{config.log_stem}.lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"WIMB audit skipped: collector already running ({lock_path}).")
                return 0

            text_path = config.log_dir / f"{config.log_stem}.log"
            jsonl_path = config.log_dir / f"{config.log_stem}.jsonl"
            try:
                with (
                    text_path.open("a", encoding="utf-8") as text_file,
                    jsonl_path.open("a", encoding="utf-8") as jsonl_file,
                ):
                    observed_at = (
                        now_provider() if now_provider is not None else datetime.now(PACIFIC)
                    )
                    if observed_at.tzinfo is None:
                        observed_at = observed_at.replace(tzinfo=PACIFIC)
                    else:
                        observed_at = observed_at.astimezone(PACIFIC)
                    result = execute(config)
                    environment = os.environ if environ is None else environ
                    dotenv_path = config.repository_root / ".env"
                    stdout = redact_secrets(result.stdout, environment, dotenv_path)
                    stderr = redact_secrets(result.stderr, environment, dotenv_path)
                    git_sha = (
                        get_git_sha(config.repository_root)
                        if git_sha_provider is None
                        else git_sha_provider(config.repository_root)
                    )
                    version = get_wimb_version() if version_provider is None else version_provider()
                    record: dict[str, object] = {
                        "observed_at": observed_at.isoformat(),
                        "timezone": PACIFIC_NAME,
                        "route_id": ROUTE_ID,
                        "direction_id": config.direction_id,
                        "direction_label": config.direction_label,
                        "stop_id": config.stop_id,
                        "stop_name": config.stop_name,
                        "requested_bus_count": config.requested_bus_count,
                        "exit_code": result.exit_code,
                        "stdout": stdout,
                        "stderr": stderr,
                        "duration_seconds": round(result.duration_seconds, 3),
                        "git_commit_sha": git_sha,
                        "wimb_version": version,
                    }
                    _append_record(text_file, jsonl_file, record)
            except OSError as error:
                return _collector_error(f"cannot write audit logs in {config.log_dir}: {error}")
    except OSError as error:
        return _collector_error(f"cannot open audit lock {lock_path}: {error}")
    return result.exit_code


def redact_secrets(value: str, environ: Mapping[str, str], dotenv_path: Path | None = None) -> str:
    redacted = value
    secrets = {
        secret
        for name, secret in environ.items()
        if secret and any(marker in name.upper() for marker in SECRET_NAME_MARKERS)
    }
    if dotenv_path is not None:
        try:
            dotenv_lines = dotenv_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            dotenv_lines = []
        for line in dotenv_lines:
            name, separator, raw_value = line.partition("=")
            if (
                separator
                and any(marker in name.strip().upper() for marker in SECRET_NAME_MARKERS)
                and (secret := raw_value.strip().strip('"').strip("'"))
            ):
                secrets.add(secret)
    for secret in sorted(secrets, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def get_git_sha(root: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603,S607 - fixed git metadata command
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def get_wimb_version() -> str | None:
    try:
        from wimb import __version__
    except ImportError:
        return None
    return __version__ or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record one Route 154 WIMB CLI audit snapshot.")
    parser.add_argument(
        "--commute", choices=tuple(COMMUTE_PRESETS), help="Named commute audit preset"
    )
    parser.add_argument("--stop", help="GTFS stop ID")
    parser.add_argument("--stop-name", help="Human-readable stop name for audit metadata")
    parser.add_argument("--direction", type=_nonnegative_int)
    parser.add_argument("--direction-label", help="Human-readable direction for audit metadata")
    parser.add_argument("--count", type=_positive_int, default=DEFAULT_BUS_COUNT)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser


def config_from_args(args: argparse.Namespace) -> AuditConfig:
    preset = COMMUTE_PRESETS.get(args.commute) if args.commute else None
    stop_id = args.stop if args.stop is not None else preset.stop_id if preset else DEFAULT_STOP_ID
    direction_id = (
        args.direction
        if args.direction is not None
        else preset.direction_id
        if preset
        else DEFAULT_DIRECTION_ID
    )
    stop_name = args.stop_name or (
        DEFAULT_STOP_NAME if stop_id == DEFAULT_STOP_ID else f"Stop {stop_id}"
    )
    if args.direction_label:
        direction_label = args.direction_label
    elif preset and args.direction is None:
        direction_label = preset.direction_label
    elif direction_id == DEFAULT_DIRECTION_ID:
        direction_label = DEFAULT_DIRECTION_LABEL
    elif preset:
        direction_label = next(
            (
                candidate.direction_label
                for candidate in COMMUTE_PRESETS.values()
                if candidate.direction_id == direction_id
            ),
            f"Direction {direction_id}",
        )
    else:
        direction_label = f"Direction {direction_id}"
    return AuditConfig(
        repository_root=repository_root(),
        stop_id=stop_id,
        stop_name=stop_name,
        direction_id=direction_id,
        direction_label=direction_label,
        requested_bus_count=args.count,
        log_dir=args.log_dir,
        timeout_seconds=args.timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_audit(config_from_args(build_parser().parse_args(argv)))


def _append_record(text_file: TextIO, jsonl_file: TextIO, record: dict[str, object]) -> None:
    stdout = str(record["stdout"])
    stderr = str(record["stderr"])
    text_file.write(
        f"=== WIMB audit {record['observed_at']} ({record['timezone']}) ===\n"
        f"Route {record['route_id']} · {record['direction_label']} "
        f"(direction_id={record['direction_id']})\n"
        f"Stop: {record['stop_name']} ({record['stop_id']}) · "
        f"requested buses: {record['requested_bus_count']}\n"
        f"exit_code={record['exit_code']} · duration_seconds={record['duration_seconds']} · "
        f"git_commit_sha={record['git_commit_sha'] or 'unavailable'} · "
        f"wimb_version={record['wimb_version'] or 'unavailable'}\n"
        "--- stdout ---\n"
    )
    text_file.write(stdout)
    if stdout and not stdout.endswith("\n"):
        text_file.write("\n")
    text_file.write("--- stderr ---\n")
    text_file.write(stderr)
    if stderr and not stderr.endswith("\n"):
        text_file.write("\n")
    text_file.write("=== end audit ===\n\n")
    json.dump(record, jsonl_file, ensure_ascii=False, sort_keys=True)
    jsonl_file.write("\n")
    text_file.flush()
    jsonl_file.flush()


def _timeout_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _collector_error(message: str) -> int:
    print(f"WIMB audit collector error: {message}", file=sys.stderr)
    return COLLECTOR_FAILURE_EXIT


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
