from __future__ import annotations

import fcntl
import json
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from scripts import audit_route_154 as audit


@pytest.fixture
def audit_config(tmp_path: Path) -> audit.AuditConfig:
    return audit.AuditConfig(
        repository_root=Path("/opt/wimb"),
        stop_id="40581",
        stop_name="North San Pedro Road Bus Pad",
        direction_id=1,
        direction_label="Southbound",
        requested_bus_count=2,
        log_dir=tmp_path,
        timeout_seconds=150,
    )


def _fixed_now() -> datetime:
    return datetime(2026, 7, 20, 5, 35, 24, tzinfo=audit.PACIFIC)


def _run(
    config: audit.AuditConfig,
    result: audit.ExecutionResult,
    *,
    environ: dict[str, str] | None = None,
    git_sha: str | None = "abc123",
    version: str | None = "0.1.0",
) -> int:
    return audit.run_audit(
        config,
        execute=lambda _config: result,
        now_provider=_fixed_now,
        environ={} if environ is None else environ,
        git_sha_provider=lambda _root: git_sha,
        version_provider=lambda: version,
    )


def _record(config: audit.AuditConfig) -> dict[str, object]:
    path = config.log_dir / f"{config.log_stem}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


def test_success_records_valid_jsonl_pacific_timestamp_and_human_output(
    audit_config: audit.AuditConfig,
) -> None:
    cli_output = (
        "Route 154\nDirection: Southbound to San Francisco\n"
        "Stop: North San Pedro Road Bus Pad\n\nBus 1 of 7\n"
    )

    exit_code = _run(audit_config, audit.ExecutionResult(0, cli_output, "", 1.23456))

    assert exit_code == 0
    record = _record(audit_config)
    assert record["observed_at"] == "2026-07-20T05:35:24-07:00"
    assert record["timezone"] == "America/Los_Angeles"
    assert record["route_id"] == "154"
    assert record["direction_id"] == 1
    assert record["direction_label"] == "Southbound"
    assert record["stop_id"] == "40581"
    assert record["stop_name"] == "North San Pedro Road Bus Pad"
    assert record["requested_bus_count"] == 2
    assert record["duration_seconds"] == 1.235
    assert record["stdout"] == cli_output
    assert record["stderr"] == ""
    assert record["git_commit_sha"] == "abc123"
    assert record["wimb_version"] == "0.1.0"

    human = (audit_config.log_dir / f"{audit_config.log_stem}.log").read_text(encoding="utf-8")
    assert "=== WIMB audit 2026-07-20T05:35:24-07:00" in human
    assert "Route 154 · Southbound (direction_id=1)" in human
    assert "--- stdout ---\n" + cli_output + "--- stderr ---\n" in human
    assert "exit_code=0 · duration_seconds=1.235" in human


def test_nonzero_wimb_exit_and_stderr_are_recorded(audit_config: audit.AuditConfig) -> None:
    result = audit.ExecutionResult(
        2, "WIMB: No live vehicles on route 154 right now.\n", "warn\n", 2
    )

    assert _run(audit_config, result) == 2
    record = _record(audit_config)
    assert record["exit_code"] == 2
    assert record["stderr"] == "warn\n"


def test_empty_stdout_is_preserved(audit_config: audit.AuditConfig) -> None:
    assert _run(audit_config, audit.ExecutionResult(3, "", "failure\n", 0.1)) == 3

    record = _record(audit_config)
    assert record["stdout"] == ""
    human = (audit_config.log_dir / f"{audit_config.log_stem}.log").read_text(encoding="utf-8")
    assert "--- stdout ---\n--- stderr ---\nfailure\n" in human


def test_execute_wimb_uses_installed_cli_cwd_arguments_and_timeout(
    audit_config: audit.AuditConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)
    times = iter((10.0, 12.5))

    result = audit.execute_wimb(audit_config, clock=lambda: next(times))

    assert captured["command"] == [
        "/opt/wimb/.venv/bin/wimb",
        "--stop",
        "40581",
        "--direction",
        "1",
        "--count",
        "2",
    ]
    assert captured["cwd"] == Path("/opt/wimb")
    assert captured["timeout"] == 150
    assert result == audit.ExecutionResult(0, "ok\n", "", 2.5)


def test_timeout_returns_distinct_failure_and_preserves_partial_output(
    audit_config: audit.AuditConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 150, output="partial\n", stderr="waiting")

    monkeypatch.setattr(audit.subprocess, "run", timeout_run)
    times = iter((20.0, 170.0))

    result = audit.execute_wimb(audit_config, clock=lambda: next(times))

    assert result.exit_code == audit.TIMEOUT_EXIT
    assert result.stdout == "partial\n"
    assert result.stderr == "waiting\nWIMB audit timed out after 150 seconds.\n"
    assert result.duration_seconds == 150


def test_lock_contention_skips_without_running_or_writing(
    audit_config: audit.AuditConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    lock_path = audit_config.log_dir / f"{audit_config.log_stem}.lock"
    called = False
    with lock_path.open("a", encoding="utf-8") as held_lock:
        fcntl.flock(held_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        def should_not_run(_config: audit.AuditConfig) -> audit.ExecutionResult:
            nonlocal called
            called = True
            return audit.ExecutionResult(0, "", "", 0)

        exit_code = audit.run_audit(audit_config, execute=should_not_run)

    assert exit_code == 0
    assert not called
    assert "collector already running" in capsys.readouterr().out
    assert not (audit_config.log_dir / f"{audit_config.log_stem}.jsonl").exists()


def test_log_write_failure_returns_collector_error(
    audit_config: audit.AuditConfig,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(audit, "_append_record", fail_write)

    exit_code = _run(audit_config, audit.ExecutionResult(0, "ok", "", 0.1))

    assert exit_code == audit.COLLECTOR_FAILURE_EXIT
    captured = capsys.readouterr()
    assert "cannot write audit logs" in captured.err
    assert "read-only filesystem" in captured.err


def test_secret_environment_values_are_redacted_from_both_logs(
    audit_config: audit.AuditConfig,
) -> None:
    secret = "super-secret-511-value"
    result = audit.ExecutionResult(
        2,
        f"accidental output {secret}\n",
        f"accidental error {secret}\n",
        0.5,
    )

    _run(audit_config, result, environ={"WIMB_API_KEY": secret})

    text = (audit_config.log_dir / f"{audit_config.log_stem}.log").read_text(encoding="utf-8")
    jsonl = (audit_config.log_dir / f"{audit_config.log_stem}.jsonl").read_text(encoding="utf-8")
    assert secret not in text
    assert secret not in jsonl
    assert "[REDACTED]" in text
    assert "[REDACTED]" in jsonl


def test_secret_loaded_by_wimb_from_dotenv_is_also_redacted(
    audit_config: audit.AuditConfig, tmp_path: Path
) -> None:
    secret = "dotenv-only-511-value"
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".env").write_text(f"WIMB_API_KEY={secret}\n", encoding="utf-8")
    config = replace(audit_config, repository_root=repository)

    _run(config, audit.ExecutionResult(2, f"bad {secret}\n", "", 0.1))

    record = _record(config)
    assert secret not in str(record["stdout"])
    assert record["stdout"] == "bad [REDACTED]\n"


def test_unavailable_git_sha_and_version_are_explicit(audit_config: audit.AuditConfig) -> None:
    _run(audit_config, audit.ExecutionResult(0, "ok\n", "", 0), git_sha=None, version=None)

    record = _record(audit_config)
    assert record["git_commit_sha"] is None
    assert record["wimb_version"] is None
    human = (audit_config.log_dir / f"{audit_config.log_stem}.log").read_text(encoding="utf-8")
    assert "git_commit_sha=unavailable" in human
    assert "wimb_version=unavailable" in human


def test_cli_argument_overrides() -> None:
    args = audit.build_parser().parse_args(
        [
            "--stop",
            "99999",
            "--stop-name",
            "Test Stop",
            "--direction",
            "0",
            "--direction-label",
            "Northbound",
            "--count",
            "4",
            "--log-dir",
            "/tmp/custom-wimb-audit",
            "--timeout",
            "45",
        ]
    )

    config = audit.config_from_args(args)

    assert config.stop_id == "99999"
    assert config.stop_name == "Test Stop"
    assert config.direction_id == 0
    assert config.direction_label == "Northbound"
    assert config.requested_bus_count == 4
    assert config.log_dir == Path("/tmp/custom-wimb-audit")
    assert config.timeout_seconds == 45
    assert config.log_stem == "route-154-99999-audit"
