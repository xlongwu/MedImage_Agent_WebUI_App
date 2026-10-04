"""The only application-runtime module allowed to start a child process."""

from __future__ import annotations

import os
import hashlib
import importlib.util
import marshal
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxProcessRequest, SandboxProcessResult


def sandbox_runtime_fingerprint() -> str:
    """Hash the current provider implementation, including frozen bytecode.

    This is a content identity, not a hash of a constant version label. Source
    and PyInstaller loaders supply different forms of the same implementation;
    inability to obtain either must fail closed rather than issue a fake hash.
    """
    digest = hashlib.sha256()
    for name in (
        "src.backend.app.runtime.windows_process_sandbox",
        "src.backend.app.runtime.sandbox_process_runner",
        "src.backend.app.schemas.sandbox",
    ):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.loader is None:
            raise SafetyError("SANDBOX_PROVIDER_FINGERPRINT_UNAVAILABLE", code="SANDBOX_PROVIDER_FINGERPRINT_UNAVAILABLE")
        source = Path(spec.origin) if spec.origin else None
        if source is not None and source.suffix == ".py" and source.is_file():
            content = source.read_bytes()
        else:
            get_code = getattr(spec.loader, "get_code", None)
            code = get_code(name) if get_code else None
            if code is None:
                raise SafetyError("SANDBOX_PROVIDER_FINGERPRINT_UNAVAILABLE", code="SANDBOX_PROVIDER_FINGERPRINT_UNAVAILABLE")
            content = marshal.dumps(code)
        digest.update(name.encode("utf-8"))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


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
