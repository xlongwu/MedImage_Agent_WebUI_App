from __future__ import annotations

import subprocess
import ctypes
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.windows_process_sandbox import WindowsProcessSandbox
from src.backend.app.runtime.windows_process_sandbox import _SecurityCapabilities, _StartupInfo, _StartupInfoEx
from src.backend.app.schemas.sandbox import SandboxProcessRequest


def _prepared_request(tmp_path):
    executable = tmp_path / "probe.exe"
    executable.write_bytes(b"test-only executable placeholder")
    for name in ("staged_input", "output", "logs", "tmp"):
        (tmp_path / name).mkdir()
    return SandboxProcessRequest(
        sandbox_id="characterization", executable_path=str(executable),
        argv=(str(executable),), cwd=str(tmp_path),
        environment={key: str(tmp_path / "tmp") for key in ("TEMP", "TMP", "LOCALAPPDATA")},
        policy_hash="test", timeout_seconds=1,
        memory_limit_bytes=16 * 1024 * 1024, max_processes=1,
    )


@pytest.mark.parametrize("assignment_ok", [True, False])
def test_process_is_assigned_before_resume_and_setup_failure_never_resumes(
    tmp_path, assignment_ok,
) -> None:
    request = _prepared_request(tmp_path)
    sandbox = object.__new__(WindowsProcessSandbox)
    calls = []
    sandbox._create_appcontainer_profile = lambda _: "appcontainer"
    sandbox.userenv = SimpleNamespace(DeleteAppContainerProfile=lambda _: 0)
    sandbox._grant_restricted_workspace_access = lambda *_args, **_kwargs: calls.append("acl")
    sandbox._verify_network_token = lambda _: calls.append("verify_network")
    sandbox.advapi32 = SimpleNamespace(FreeSid=lambda _: calls.append("free_sid"))
    sandbox._create_job = lambda _: "job"
    sandbox._create_restricted_token = lambda: "token"
    sandbox._open_inheritable_log = lambda _: (None, "log")
    sandbox._create_suspended_process = lambda *_: ("process", "thread")
    sandbox._wait = lambda *_: ("SUCCEEDED", 0, None)
    sandbox.kernel32 = SimpleNamespace(
        AssignProcessToJobObject=lambda *_: calls.append("assign") or assignment_ok,
        ResumeThread=lambda *_: calls.append("resume") or 0,
        TerminateJobObject=lambda *_: calls.append("terminate") or 1,
        TerminateProcess=lambda *_: calls.append("terminate_process") or 1,
        WaitForSingleObject=lambda *_: 0,
        CloseHandle=lambda handle: calls.append(f"close:{handle}") or 1,
    )
    # Do not read platform last-error state when exercising fake Win32 calls.
    sandbox._failed_start = lambda: SafetyError("start", code="SANDBOX_PROCESS_START_FAILED")
    if assignment_ok:
        result = sandbox.run(request, timeout_seconds=1)
        assert result.status == "SUCCEEDED"
        assert result.return_code == 0
        assert result.started_at <= result.ended_at <= datetime.now(UTC)
        assert calls.index("assign") < calls.index("resume")
        assert calls.index("verify_network") < calls.index("resume")
        assert result.network_isolation == "enforced"
    else:
        with pytest.raises(SafetyError):
            sandbox.run(request, timeout_seconds=1)
        assert "resume" not in calls
        assert "terminate" in calls
    assert all(f"close:{handle}" in calls for handle in ("thread", "process", "token", "job"))


@pytest.mark.parametrize("cancelled", [True, False])
def test_timeout_and_cancel_terminate_owned_job_before_return(cancelled) -> None:
    sandbox = object.__new__(WindowsProcessSandbox)
    calls = []
    sandbox.kernel32 = SimpleNamespace(
        WaitForSingleObject=lambda *_: calls.append("wait") or 258,
        TerminateJobObject=lambda *_: calls.append("terminate") or 1,
    )
    result = sandbox._wait("job", "process", 1, lambda: cancelled)
    assert result == (("CANCELLED", None, "cancelled") if cancelled else ("TIMED_OUT", None, "timeout"))
    assert calls[-2:] == ["terminate", "wait"]


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
        sandbox._grant_restricted_workspace_access(tmp_path, appcontainer_sid=None)
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


def test_security_capabilities_match_windows_sdk_pointer_layout() -> None:
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    assert _SecurityCapabilities.CapabilityCount.offset == 2 * pointer_size
    assert ctypes.sizeof(_SecurityCapabilities) == 2 * pointer_size + 8
    assert _StartupInfoEx.lpAttributeList.offset == ctypes.sizeof(_StartupInfo)
    assert ctypes.sizeof(_StartupInfoEx) == ctypes.sizeof(_StartupInfo) + pointer_size


@pytest.mark.parametrize("is_appcontainer,capability_count", [(0, 0), (1, 1), (1, 0)])
def test_actual_child_token_must_be_appcontainer_with_zero_capabilities(is_appcontainer, capability_count) -> None:
    sandbox = object.__new__(WindowsProcessSandbox)
    closed = []

    def open_token(_process, _access, token_pointer):
        ctypes.cast(token_pointer, ctypes.POINTER(ctypes.c_void_p)).contents.value = 123
        return 1

    def get_information(_token, kind, data, _size, returned_size):
        ctypes.cast(data, ctypes.POINTER(ctypes.c_uint32)).contents.value = is_appcontainer if kind == 29 else capability_count
        ctypes.cast(returned_size, ctypes.POINTER(ctypes.c_uint32)).contents.value = 4 if kind == 29 else 8
        return 1

    sandbox.advapi32 = SimpleNamespace(OpenProcessToken=open_token, GetTokenInformation=get_information)
    sandbox.kernel32 = SimpleNamespace(CloseHandle=lambda handle: closed.append(handle.value))
    if is_appcontainer and not capability_count:
        sandbox._verify_network_token("child")
    else:
        with pytest.raises(SafetyError) as error:
            sandbox._verify_network_token("child")
        assert error.value.code == "SANDBOX_NETWORK_SETUP_FAILED"
    assert closed == [123]


def test_appcontainer_acl_is_readonly_for_inputs_and_writable_only_for_mutable_dirs(tmp_path) -> None:
    sandbox = object.__new__(WindowsProcessSandbox)
    calls = []
    for name in ("staged_input", "output", "logs", "tmp", "meta"):
        (tmp_path / name).mkdir()
    (tmp_path / "staged_input" / "input.txt").write_text("input")
    sid = object()
    sandbox._grant_restricted_path_access = lambda target, **kwargs: calls.append((target, kwargs))
    sandbox._grant_restricted_workspace_access(tmp_path, appcontainer_sid=sid)
    grants = [(target, entry) for target, entry in calls if entry.get("explicit_sid") is sid]
    writes = {target.name for target, entry in grants if entry["access_mask"] == 0x10000000}
    assert writes == {"output", "logs", "tmp"}
    assert all(target.name != "meta" for target, _entry in grants)
    assert any(target.name == "input.txt" and entry["access_mask"] == 0xA0000000 for target, entry in grants)
