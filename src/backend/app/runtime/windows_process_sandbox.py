"""Fail-closed Windows restricted-token process provider.

Only ``SandboxProcessRunner`` may reach this module. A process is created
suspended with a write-restricted token, placed in a kill-on-close Job Object,
and resumed only after all setup succeeds. There is no normal-process fallback.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxProcessRequest, SandboxProcessResult

_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_STARTF_USESTDHANDLES = 0x00000100
_HANDLE_FLAG_INHERIT = 1
_WAIT_OBJECT_0, _WAIT_TIMEOUT, _INFINITE = 0, 258, 0xFFFFFFFF
_TOKEN_ASSIGN_PRIMARY, _TOKEN_DUPLICATE, _TOKEN_QUERY, _TOKEN_ADJUST_DEFAULT = 1, 2, 8, 0x80
_DISABLE_MAX_PRIVILEGE, _WRITE_RESTRICTED = 1, 8
_KILL_ON_JOB_CLOSE, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 0x00002000, 9
_JOB_OBJECT_LIMIT_PROCESS_MEMORY, _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x100, 8
_DACL_SECURITY_INFORMATION, _SE_FILE_OBJECT = 4, 1
_GRANT_ACCESS, _TRUSTEE_IS_SID, _TRUSTEE_IS_UNKNOWN = 1, 0, 0
_SUB_CONTAINERS_AND_OBJECTS_INHERIT, _GENERIC_ALL, _WIN_RESTRICTED_CODE_SID = 3, 0x10000000, 33


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


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_uint32), ("lpReserved", ctypes.c_wchar_p), ("lpDesktop", ctypes.c_wchar_p), ("lpTitle", ctypes.c_wchar_p), ("dwX", ctypes.c_uint32), ("dwY", ctypes.c_uint32), ("dwXSize", ctypes.c_uint32), ("dwYSize", ctypes.c_uint32), ("dwXCountChars", ctypes.c_uint32), ("dwYCountChars", ctypes.c_uint32), ("dwFillAttribute", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32), ("wShowWindow", ctypes.c_ushort), ("cbReserved2", ctypes.c_ushort), ("lpReserved2", ctypes.c_void_p), ("hStdInput", ctypes.c_void_p), ("hStdOutput", ctypes.c_void_p), ("hStdError", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", ctypes.c_void_p), ("hThread", ctypes.c_void_p), ("dwProcessId", ctypes.c_uint32), ("dwThreadId", ctypes.c_uint32)]


class _Trustee(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_uint32), ("TrusteeForm", ctypes.c_uint32), ("TrusteeType", ctypes.c_uint32), ("ptstrName", ctypes.c_void_p)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", ctypes.c_uint32), ("grfAccessMode", ctypes.c_uint32), ("grfInheritance", ctypes.c_uint32), ("Trustee", _Trustee)]


class WindowsProcessSandbox:
    def __init__(self) -> None:
        if os.name != "nt":
            raise SafetyError("SANDBOX_PROVIDER_UNAVAILABLE", code="SANDBOX_PROVIDER_UNAVAILABLE")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    def run(self, request: SandboxProcessRequest, *, timeout_seconds: int, cancel_requested: Callable[[], bool] | None = None) -> SandboxProcessResult:
        self._validate(request, timeout_seconds)
        cwd = Path(request.cwd).resolve()
        logs = cwd / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        stdout_path, stderr_path = logs / "stdout.log", logs / "stderr.log"
        self._grant_restricted_workspace_access(cwd)
        started = datetime.now(UTC)
        job = token = process = thread = None
        stdout_fd = stderr_fd = None
        try:
            job = self._create_job(request)
            token = self._create_restricted_token()
            stdout_fd, stdout = self._open_inheritable_log(stdout_path)
            stderr_fd, stderr = self._open_inheritable_log(stderr_path)
            process, thread = self._create_suspended_process(token, request, stdout, stderr)
            if not self.kernel32.AssignProcessToJobObject(job, process):
                raise self._failed_start()
            if self.kernel32.ResumeThread(thread) == 0xFFFFFFFF:
                raise self._failed_start()
            status, return_code, reason = self._wait(job, process, timeout_seconds, cancel_requested)
            return SandboxProcessResult(sandbox_id=request.sandbox_id, status=status, return_code=return_code, started_at=started, ended_at=datetime.now(UTC), terminated_reason=reason, stdout_path=str(stdout_path), stderr_path=str(stderr_path))
        except SafetyError:
            if job:
                self.kernel32.TerminateJobObject(job, 1)
            raise
        except Exception as exc:
            if job:
                self.kernel32.TerminateJobObject(job, 1)
            raise self._failed_start() from exc
        finally:
            for handle in (thread, process, token, job):
                if handle:
                    self.kernel32.CloseHandle(handle)
            for fd in (stdout_fd, stderr_fd):
                if fd is not None:
                    os.close(fd)

    def _create_job(self, request: SandboxProcessRequest) -> ctypes.c_void_p:
        job = self.kernel32.CreateJobObjectW(None, None)
        if not job:
            raise self._failed_start()
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        limits.BasicLimitInformation.ActiveProcessLimit = request.max_processes
        limits.ProcessMemoryLimit = request.memory_limit_bytes
        if not self.kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.kernel32.CloseHandle(job)
            raise self._failed_start()
        return job

    def _create_restricted_token(self) -> ctypes.c_void_p:
        current, restricted = ctypes.c_void_p(), ctypes.c_void_p()
        mask = _TOKEN_ASSIGN_PRIMARY | _TOKEN_DUPLICATE | _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT
        if not self.advapi32.OpenProcessToken(self.kernel32.GetCurrentProcess(), mask, ctypes.byref(current)):
            raise self._failed_start()
        try:
            if not self.advapi32.CreateRestrictedToken(current, _DISABLE_MAX_PRIVILEGE | _WRITE_RESTRICTED, 0, None, 0, None, 0, None, ctypes.byref(restricted)):
                raise self._failed_start()
            return restricted
        finally:
            self.kernel32.CloseHandle(current)

    def _grant_restricted_workspace_access(self, cwd: Path) -> None:
        """Write-restricted token may write only where its SID is granted access."""
        dacl = descriptor = new_dacl = ctypes.c_void_p()
        sid = (ctypes.c_byte * 128)()
        size = ctypes.c_uint32(ctypes.sizeof(sid))
        try:
            if self.advapi32.GetNamedSecurityInfoW(str(cwd), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None, ctypes.byref(descriptor)) != 0:
                raise SafetyError("SANDBOX_ACL_SETUP_FAILED", code="SANDBOX_ACL_SETUP_FAILED")
            if not self.advapi32.CreateWellKnownSid(_WIN_RESTRICTED_CODE_SID, None, ctypes.byref(sid), ctypes.byref(size)):
                raise SafetyError("SANDBOX_ACL_SETUP_FAILED", code="SANDBOX_ACL_SETUP_FAILED")
            entry = _ExplicitAccess(_GENERIC_ALL, _GRANT_ACCESS, _SUB_CONTAINERS_AND_OBJECTS_INHERIT, _Trustee(None, 0, _TRUSTEE_IS_SID, _TRUSTEE_IS_UNKNOWN, ctypes.cast(sid, ctypes.c_void_p)))
            if self.advapi32.SetEntriesInAclW(1, ctypes.byref(entry), dacl, ctypes.byref(new_dacl)) != 0:
                raise SafetyError("SANDBOX_ACL_SETUP_FAILED", code="SANDBOX_ACL_SETUP_FAILED")
            if self.advapi32.SetNamedSecurityInfoW(str(cwd), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, new_dacl, None) != 0:
                raise SafetyError("SANDBOX_ACL_SETUP_FAILED", code="SANDBOX_ACL_SETUP_FAILED")
        finally:
            for pointer in (new_dacl, descriptor):
                if pointer:
                    self.kernel32.LocalFree(pointer)

    def _open_inheritable_log(self, path: Path) -> tuple[int, ctypes.c_void_p]:
        import msvcrt
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_BINARY)
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(fd))
        if not self.kernel32.SetHandleInformation(handle, _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT):
            os.close(fd)
            raise self._failed_start()
        return fd, handle

    def _create_suspended_process(self, token: ctypes.c_void_p, request: SandboxProcessRequest, stdout: ctypes.c_void_p, stderr: ctypes.c_void_p) -> tuple[ctypes.c_void_p, ctypes.c_void_p]:
        startup, info = _StartupInfo(), _ProcessInformation()
        startup.cb, startup.dwFlags = ctypes.sizeof(startup), _STARTF_USESTDHANDLES
        startup.hStdInput, startup.hStdOutput, startup.hStdError = ctypes.c_void_p(), stdout, stderr
        command = ctypes.create_unicode_buffer(self._command_line(request.argv))
        env = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(request.environment.items(), key=lambda item: item[0].casefold())) + "\0\0")
        if not self.advapi32.CreateProcessAsUserW(token, request.executable_path, command, None, None, True, _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT, env, request.cwd, ctypes.byref(startup), ctypes.byref(info)):
            raise self._failed_start()
        return info.hProcess, info.hThread

    def _wait(self, job: ctypes.c_void_p, process: ctypes.c_void_p, timeout_seconds: int, cancel_requested: Callable[[], bool] | None) -> tuple[str, int | None, str | None]:
        elapsed = 0
        while elapsed < timeout_seconds * 1000:
            result = self.kernel32.WaitForSingleObject(process, min(250, timeout_seconds * 1000 - elapsed))
            if result == _WAIT_OBJECT_0:
                code = ctypes.c_uint32()
                if not self.kernel32.GetExitCodeProcess(process, ctypes.byref(code)):
                    raise self._failed_start()
                return ("SUCCEEDED" if code.value == 0 else "FAILED", int(code.value), None)
            if result != _WAIT_TIMEOUT:
                raise self._failed_start()
            if cancel_requested and cancel_requested():
                self.kernel32.TerminateJobObject(job, 1)
                self.kernel32.WaitForSingleObject(process, _INFINITE)
                return "CANCELLED", None, "cancelled"
            elapsed += 250
        self.kernel32.TerminateJobObject(job, 1)
        self.kernel32.WaitForSingleObject(process, _INFINITE)
        return "TIMED_OUT", None, "timeout"

    @staticmethod
    def _command_line(argv: tuple[str, ...]) -> str:
        def quote(value: str) -> str:
            if value and not any(char in value for char in ' \t"'):
                return value
            return '"' + value.replace("\\", "\\\\").replace('"', '\\\"') + '"'
        return " ".join(quote(value) for value in argv)

    @staticmethod
    def _validate(request: SandboxProcessRequest, timeout_seconds: int) -> None:
        cwd, executable = Path(request.cwd).resolve(), Path(request.executable_path).resolve()
        if timeout_seconds != request.timeout_seconds or not request.argv or Path(request.argv[0]).resolve() != executable or not executable.is_file():
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
        if not cwd.is_dir() or any(key.upper() in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "COOKIE", "TOKEN"} for key in request.environment):
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
        if request.environment.get("TEMP") != str(cwd / "tmp") or request.environment.get("TMP") != str(cwd / "tmp"):
            raise SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")

    @staticmethod
    def _failed_start() -> SafetyError:
        return SafetyError("SANDBOX_PROCESS_START_FAILED", code="SANDBOX_PROCESS_START_FAILED")
