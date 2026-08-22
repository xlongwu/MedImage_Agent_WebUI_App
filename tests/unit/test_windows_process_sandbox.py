from __future__ import annotations

import subprocess

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.windows_process_sandbox import WindowsProcessSandbox


def test_windows_command_line_uses_standard_windows_quoting() -> None:
    argv = (
        r"C:\Program Files\Tool\tool.exe",
        "plain",
        "value with spaces",
        'embedded"quote',
    )

    assert WindowsProcessSandbox._command_line(argv) == subprocess.list2cmdline(
        list(argv)
    )


def test_restricted_workspace_requires_every_mutable_directory(tmp_path) -> None:
    sandbox = object.__new__(WindowsProcessSandbox)
    sandbox._grant_restricted_path_access = lambda *_args, **_kwargs: None
    (tmp_path / "staged_input").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "logs").mkdir()

    try:
        sandbox._grant_restricted_workspace_access(tmp_path)
    except Exception as exc:
        assert getattr(exc, "code", None) == "SANDBOX_ACL_SETUP_FAILED"
    else:  # pragma: no cover - documents the fail-closed contract
        raise AssertionError("missing tmp directory must fail closed")


def test_windows_failure_diagnostics_are_redacted_and_structured() -> None:
    error = WindowsProcessSandbox._failed_start(
        "SANDBOX_TOKEN_SETUP_FAILED",
        stage="create_restricted_token",
        winerror=87,
    )

    assert isinstance(error, SafetyError)
    assert error.code == "SANDBOX_TOKEN_SETUP_FAILED"
    assert error.details == {"stage": "create_restricted_token", "winerror": 87}
    assert "path" not in error.details
    assert "command" not in error.details
