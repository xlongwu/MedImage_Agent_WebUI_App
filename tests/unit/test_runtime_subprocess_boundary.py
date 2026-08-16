"""The deterministic runtime has one process-provider seam."""

from __future__ import annotations

from pathlib import Path


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
