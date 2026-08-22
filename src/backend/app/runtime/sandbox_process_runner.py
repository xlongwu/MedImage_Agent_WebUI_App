"""The only application-runtime module allowed to start a child process."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxProcessRequest, SandboxProcessResult


def reject_unreviewed_process_start(*_args, **_kwargs):
    """Fail closed for retired process-launch paths outside the gateway."""

    raise SafetyError(
        "EXECUTION_CONTRACT_REQUIRED", code="EXECUTION_CONTRACT_REQUIRED"
    )


class UnsupportedSandboxProcessRunner:
    def run(
        self,
        request: SandboxProcessRequest,
        *,
        timeout_seconds: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SandboxProcessResult:
        raise SafetyError("SANDBOX_PROVIDER_UNAVAILABLE", code="SANDBOX_PROVIDER_UNAVAILABLE")


class SandboxProcessRunner:
    """Route internal requests to the platform implementation without fallback."""

    def __init__(self) -> None:
        self._runner = None

    def run(
        self,
        request: SandboxProcessRequest,
        *,
        timeout_seconds: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> SandboxProcessResult:
        if os.name != "nt":
            return UnsupportedSandboxProcessRunner().run(
                request,
                timeout_seconds=timeout_seconds,
                cancel_requested=cancel_requested,
            )
        if self._runner is None:
            from src.backend.app.runtime.windows_process_sandbox import WindowsProcessSandbox
            self._runner = WindowsProcessSandbox()
        return self._runner.run(
            request,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )
