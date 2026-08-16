"""Windows Job Object implementation for a gateway-owned sandbox process.

The provider is deliberately fail-closed: process setup errors never fall back
to normal ``subprocess`` execution.  No current external node is executable;
this component is therefore present for reviewed future contracts only.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxProcessRequest, SandboxProcessResult


_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008


class _LargeInteger(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LargeInteger), ("PerJobUserTimeLimit", _LargeInteger),
        ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation), ("IoInfo", ctypes.c_byte * 48),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsProcessSandbox:
    def __init__(self) -> None:
        if os.name != "nt":
            raise SafetyError("SANDBOX_PROVIDER_UNAVAILABLE", code="SANDBOX_PROVIDER_UNAVAILABLE")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def run(self, request: SandboxProcessRequest, *, timeout_seconds: int) -> SandboxProcessResult:
        self._validate(request)
        logs = Path(request.cwd) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path, stderr_path = logs / "stdout.log", logs / "stderr.log"
        started = datetime.now(UTC)
        job = self.kernel32.CreateJobObjectW(None, None)
        if not job:
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
        process = None
        try:
            limits = _ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            limits.BasicLimitInformation.ActiveProcessLimit = 8
            if not self.kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                try:
                    process = subprocess.Popen(
                        list(request.argv), cwd=request.cwd, env=request.environment,
                        shell=False, stdout=stdout, stderr=stderr,
                        creationflags=getattr(subprocess, "CREATE_SUSPENDED", 0),
                    )
                except OSError as exc:
                    raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED") from exc
                if not self.kernel32.AssignProcessToJobObject(job, ctypes.c_void_p(process._handle)):
                    process.kill()
                    process.wait(timeout=5)
                    raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
                if not self.kernel32.ResumeThread(ctypes.c_void_p(process._thread)):
                    self.kernel32.TerminateJobObject(job, 1)
                    raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
                try:
                    return_code = process.wait(timeout=timeout_seconds)
                    status = "SUCCEEDED" if return_code == 0 else "FAILED"
                    reason = None
                except subprocess.TimeoutExpired:
                    self.kernel32.TerminateJobObject(job, 1)
                    process.wait(timeout=5)
                    return_code, status, reason = None, "TIMED_OUT", "timeout"
            return SandboxProcessResult(
                sandbox_id=request.sandbox_id, status=status, return_code=return_code,
                started_at=started, ended_at=datetime.now(UTC), terminated_reason=reason,
                stdout_path=str(stdout_path), stderr_path=str(stderr_path),
            )
        finally:
            if job:
                self.kernel32.CloseHandle(job)

    @staticmethod
    def _validate(request: SandboxProcessRequest) -> None:
        cwd = Path(request.cwd).resolve()
        executable = Path(request.executable_path).resolve()
        if not request.argv or Path(request.argv[0]).resolve() != executable or not executable.is_file():
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
        if not cwd.is_dir() or any(key.upper() in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "COOKIE", "TOKEN"} for key in request.environment):
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
        if request.environment.get("TEMP") != str(cwd / "tmp") or request.environment.get("TMP") != str(cwd / "tmp"):
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
