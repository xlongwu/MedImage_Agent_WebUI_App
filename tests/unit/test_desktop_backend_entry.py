from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.app.desktop_backend_entry import (
    APP_IMPORT_STRING,
    DEFAULT_DESKTOP_HOST,
    DESKTOP_PARENT_PID_ENV,
    DesktopBackendConfig,
    _desktop_parent_pid,
    _sandbox_self_test_argv,
    _watch_parent_process,
    ensure_packaged_windows_runtime_dirs,
    main,
    parse_args,
    run_backend,
    validate_host,
)
from src.backend.app.schemas.sandbox import SandboxProcessResult
from src.backend.app.main import app


def test_desktop_backend_entry_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MEDIMAGE_DESKTOP_BACKEND_HOST", raising=False)
    monkeypatch.delenv("MEDIMAGE_DESKTOP_BACKEND_PORT", raising=False)
    config = parse_args([])

    assert config.host == DEFAULT_DESKTOP_HOST
    assert config.port == 8765


def test_desktop_backend_entry_reads_port_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MEDIMAGE_DESKTOP_BACKEND_PORT", "8999")
    config = parse_args([])

    assert config.port == 8999


def test_desktop_backend_entry_rejects_non_loopback_host():
    with pytest.raises(ValueError, match="127.0.0.1"):
        validate_host("0.0.0.0")


def test_desktop_backend_entry_runs_uvicorn_without_reload(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_run(app_import: str, **kwargs: object) -> None:
        captured["app_import"] = app_import
        captured.update(kwargs)

    monkeypatch.setattr("src.backend.app.desktop_backend_entry.uvicorn.run", fake_run)
    monkeypatch.delenv(DESKTOP_PARENT_PID_ENV, raising=False)
    run_backend(DesktopBackendConfig(host="127.0.0.1", port=8765, log_level="info"))

    assert captured["app_import"] == APP_IMPORT_STRING
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert captured["reload"] is False
    assert captured["factory"] is False


def test_sandbox_self_test_rejects_unknown_case_without_starting_backend(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr("src.backend.app.desktop_backend_entry._is_windows_runtime", lambda: False)
    assert main(["--sandbox-self-test", "unknown"]) == 2
    assert "SANDBOX_PROVIDER_UNAVAILABLE" in capsys.readouterr().out


def test_sandbox_self_test_uses_fixed_windows_helper_argv(tmp_path: Path) -> None:
    memory_input = tmp_path / "memory-input.txt"
    argv = _sandbox_self_test_argv("write_allowed_output", memory_input)

    assert argv[0].endswith("System32\\cmd.exe")
    assert argv[1:4] == ("/d", "/q", "/c")
    assert "output\\proof.txt" in argv[-1]

    timeout_argv = _sandbox_self_test_argv("timeout", memory_input)
    assert timeout_argv[0].lower().endswith("system32\\ping.exe")

    memory_argv = _sandbox_self_test_argv("memory_limit", memory_input)
    assert memory_argv[0].lower().endswith("system32\\sort.exe")
    assert memory_argv[-1] == str(memory_input)


def test_sandbox_self_test_cleans_its_fixed_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    now = datetime.now(UTC)
    captured: dict[str, object] = {}

    def fake_run(request, *, timeout_seconds: int):
        captured["request"] = request
        assert timeout_seconds == request.timeout_seconds
        proof = Path(request.cwd) / "output" / "proof.txt"
        proof.write_text("ok", encoding="ascii")
        return SandboxProcessResult(
            sandbox_id=request.sandbox_id, status="SUCCEEDED", return_code=0,
            started_at=now, ended_at=now, stdout_path="redacted", stderr_path="redacted",
        )

    monkeypatch.setattr("src.backend.app.desktop_backend_entry._is_windows_runtime", lambda: True)
    monkeypatch.setattr("src.backend.app.desktop_backend_entry._run_sandbox_self_test_process", fake_run)
    monkeypatch.chdir(tmp_path)

    assert main(["--sandbox-self-test", "write_allowed_output"]) == 0
    request = captured["request"]
    assert request.executable_path.endswith("System32\\cmd.exe")
    assert request.argv[0] == request.executable_path
    assert "output\\proof.txt" in request.argv[-1]
    assert request.environment == {"TEMP": str(Path(request.cwd) / "tmp"), "TMP": str(Path(request.cwd) / "tmp")}
    assert not list(tmp_path.glob(".sandbox-self-test-*"))
    assert '"network_isolation": "not_enforced"' in capsys.readouterr().out


def test_sandbox_self_test_reports_restricted_process_start_failure(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
    class StartFailure(Exception):
        code = "SANDBOX_PROCESS_START_FAILED"
        details = {"stage": "create_restricted_token", "winerror": 87}

    monkeypatch.setattr("src.backend.app.desktop_backend_entry._is_windows_runtime", lambda: True)
    monkeypatch.setattr(
        "src.backend.app.desktop_backend_entry._run_sandbox_self_test_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(StartFailure()),
    )
    monkeypatch.chdir(tmp_path)

    assert main(["--sandbox-self-test", "write_allowed_output"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": False,
        "code": "SANDBOX_PROCESS_START_FAILED",
        "stage": "create_restricted_token",
        "winerror": 87,
    }


def test_desktop_parent_pid_accepts_only_a_distinct_positive_pid(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(DESKTOP_PARENT_PID_ENV, "4242")
    monkeypatch.setattr("src.backend.app.desktop_backend_entry.os.getpid", lambda: 100)
    assert _desktop_parent_pid() == 4242

    for invalid in ("", "not-a-pid", "0", "-1", "100"):
        monkeypatch.setenv(DESKTOP_PARENT_PID_ENV, invalid)
        assert _desktop_parent_pid() is None


def test_desktop_parent_watchdog_exits_after_parent_disappears():
    alive_states = iter((True, False))
    exit_codes: list[int] = []

    _watch_parent_process(
        4242,
        poll_interval=0,
        is_alive=lambda _pid: next(alive_states),
        exit_process=lambda code: exit_codes.append(code),
    )

    assert exit_codes == [0]


def test_frozen_windows_runtime_bin_stays_inside_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setattr("src.backend.app.desktop_backend_entry._is_windows_runtime", lambda: True)
    monkeypatch.setenv("MEDIMAGE_DESKTOP_WORKSPACE", str(tmp_path))

    created = ensure_packaged_windows_runtime_dirs()

    assert created == (tmp_path / "bin",)
    assert (tmp_path / "bin").is_dir()


def test_windows_runtime_bin_is_noop_without_desktop_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.setattr("src.backend.app.desktop_backend_entry._is_windows_runtime", lambda: True)
    monkeypatch.delenv("MEDIMAGE_DESKTOP_WORKSPACE", raising=False)

    assert ensure_packaged_windows_runtime_dirs() == ()
    assert not (tmp_path / "bin").exists()


def test_backend_health_endpoints_available_for_desktop_shell():
    client = TestClient(app)

    root_health = client.get("/health")
    api_health = client.get("/api/health")

    assert root_health.status_code == 200
    assert root_health.json()["ok"] is True
    assert api_health.status_code == 200


def test_backend_health_proves_desktop_sidecar_identity(monkeypatch: pytest.MonkeyPatch):
    token = "desktop-session-token"
    nonce = "sidecar-health-nonce"
    monkeypatch.setenv("MEDIMAGE_DESKTOP_SESSION_TOKEN", token)
    client = TestClient(app)

    challenged = client.get(
        "/api/health",
        headers={"X-MedImage-Desktop-Health-Nonce": nonce},
    )
    unchallenged = client.get("/api/health")

    expected = hmac.new(
        token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    assert challenged.status_code == 200
    assert challenged.headers["X-MedImage-Desktop-Health-Proof"] == expected
    assert "X-MedImage-Desktop-Health-Proof" not in unchallenged.headers
