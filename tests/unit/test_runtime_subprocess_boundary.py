"""The deterministic runtime has one process-provider seam."""

from __future__ import annotations

from pathlib import Path

from src.backend.app.main import create_app


def test_runtime_process_calls_are_confined_to_windows_provider() -> None:
    runtime = Path("src/backend/app/runtime")
    offenders = []
    for path in runtime.rglob("*.py"):
        if path.name in {"sandbox_process_runner.py", "windows_process_sandbox.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        if "subprocess.run(" in text or "subprocess.Popen(" in text:
            offenders.append(path.as_posix())
    assert offenders == []


def test_legacy_sandbox_routes_are_not_exposed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert not any("execute-sandbox" in path or "register-sandbox-" in path for path in paths)
    assert "/api/projects/{project_id}/runs/{run_id}/sandbox-attempts" in paths
