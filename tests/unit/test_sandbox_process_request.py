from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.windows_process_sandbox import WindowsProcessSandbox
from src.backend.app.schemas.sandbox import SandboxProcessRequest


def _request(tmp_path: Path) -> SandboxProcessRequest:
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"fixed-test-placeholder")
    (tmp_path / "tmp").mkdir()
    return SandboxProcessRequest(
        sandbox_id="sandbox",
        executable_path=str(executable),
        argv=(str(executable), "--fixed"),
        cwd=str(tmp_path),
        environment={"TEMP": str(tmp_path / "tmp"), "TMP": str(tmp_path / "tmp")},
        policy_hash="policy",
        timeout_seconds=10,
        memory_limit_bytes=16 * 1024 * 1024,
        max_processes=1,
    )


def test_process_request_rejects_executable_argv_mismatch(tmp_path: Path) -> None:
    request = _request(tmp_path).model_copy(
        update={"argv": (str(tmp_path / "other.exe"),)}
    )

    with pytest.raises(SafetyError) as exc_info:
        WindowsProcessSandbox._validate(request, 10)

    assert exc_info.value.code == "SANDBOX_PROCESS_START_FAILED"


def test_process_request_rejects_sensitive_environment(tmp_path: Path) -> None:
    request = _request(tmp_path)
    request = request.model_copy(
        update={"environment": {**request.environment, "OPENAI_API_KEY": "secret"}}
    )

    with pytest.raises(SafetyError) as exc_info:
        WindowsProcessSandbox._validate(request, 10)

    assert exc_info.value.code == "SANDBOX_PROCESS_START_FAILED"


def test_process_request_accepts_fixed_executable_and_isolated_temp(tmp_path: Path) -> None:
    request = _request(tmp_path)

    WindowsProcessSandbox._validate(request, 10)
