from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Callable

import uvicorn

# PyInstaller imports this module from the frozen bundle. Source-tree smoke
# invokes the file directly, so make the repository package root available
# without using a caller-controlled environment variable.
if __package__ in {None, ""}:
    _SOURCE_ROOT = Path(__file__).resolve().parents[3]
    if str(_SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(_SOURCE_ROOT))

DEFAULT_DESKTOP_HOST = "127.0.0.1"
DEFAULT_DESKTOP_PORT = 8765
APP_IMPORT_STRING = "src.backend.app.main:app"
DESKTOP_PARENT_PID_ENV = "MEDIMAGE_DESKTOP_PARENT_PID"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SANDBOX_CHILD_STARTED_MARKER = "MEDIMAGE_SANDBOX_CHILD_STARTED"
_SANDBOX_SELF_TEST_CASES = {
    "write_allowed_output", "write_rawdata_denied", "write_outside_project_denied",
    "spawn_child_tree", "memory_limit", "timeout", "print_environment_keys",
    "network_loopback_ipv4", "network_loopback_ipv6", "network_host_ipv4",
}
_SANDBOX_SELF_TEST_COMMANDS = {
    "write_allowed_output": "echo ok>output\\proof.txt",
    "write_rawdata_denied": (
        "echo blocked>..\\rawdata\\blocked.txt 2>nul & "
        "if exist ..\\rawdata\\blocked.txt (exit /b 1) else (exit /b 0)"
    ),
    "write_outside_project_denied": (
        "mkdir ..\\outside 2>nul & "
        "if exist ..\\outside (exit /b 1) else (exit /b 0)"
    ),
    "print_environment_keys": (
        "if defined OPENAI_API_KEY (exit /b 1) else if defined ANTHROPIC_API_KEY "
        "(exit /b 1) else if defined COOKIE (exit /b 1) else if defined TOKEN "
        "(exit /b 1) else (exit /b 0)"
    ),
}


@dataclass(frozen=True)
class DesktopBackendConfig:
    host: str
    port: int
    log_level: str


def _is_windows_runtime() -> bool:
    return os.name == "nt"


def _desktop_parent_pid() -> int | None:
    """Return the Electron main-process PID supplied to the managed sidecar."""
    raw = os.environ.get(DESKTOP_PARENT_PID_ENV, "").strip()
    if not raw:
        return None
    try:
        parent_pid = int(raw)
    except ValueError:
        return None
    if parent_pid <= 0 or parent_pid == os.getpid():
        return None
    return parent_pid


def _parent_process_is_alive(parent_pid: int) -> bool:
    """Check the desktop parent without opening a broad process handle."""
    if _is_windows_runtime():
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            parent_pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watch_parent_process(
    parent_pid: int,
    *,
    poll_interval: float = 1.0,
    is_alive: Callable[[int], bool] = _parent_process_is_alive,
    exit_process: Callable[[int], object] = os._exit,
) -> None:
    """Exit a managed sidecar if its Electron owner disappears unexpectedly."""
    while is_alive(parent_pid):
        time.sleep(poll_interval)
    exit_process(0)


def start_parent_watchdog() -> threading.Thread | None:
    """Start the parent watchdog only for Electron-managed backend processes."""
    parent_pid = _desktop_parent_pid()
    if parent_pid is None:
        return None
    watchdog = threading.Thread(
        target=_watch_parent_process,
        args=(parent_pid,),
        name="medimage-desktop-parent-watchdog",
        daemon=True,
    )
    watchdog.start()
    return watchdog


def ensure_packaged_windows_runtime_dirs() -> tuple[Path, ...]:
    """Create frozen-runtime probe directories inside the desktop workspace.

    CuPy's Windows loader probes ``<launch workspace>/bin`` during its lazy
    import.  The directory is absent in a fresh desktop workspace, so the probe
    raises ``WinError 2`` before CuPy can load bundled DLLs unless the empty
    directory exists.

    The helper is a no-op outside a Windows desktop process and creates only a
    direct child of the explicitly selected desktop workspace.
    """
    workspace_value = os.environ.get("MEDIMAGE_DESKTOP_WORKSPACE")
    if not _is_windows_runtime() or not workspace_value:
        return ()
    workspace = Path(workspace_value).expanduser().resolve()
    runtime_bin = (workspace / "bin").resolve()
    if runtime_bin.parent != workspace:
        raise RuntimeError(
            f"Frozen runtime bin directory escapes desktop workspace: {runtime_bin}"
        )
    runtime_bin.mkdir(parents=True, exist_ok=True)
    return (runtime_bin,)


def validate_host(host: str) -> str:
    normalized = host.strip()
    if normalized != DEFAULT_DESKTOP_HOST:
        raise ValueError("Desktop backend host must be 127.0.0.1.")
    return normalized


def _env_port() -> int:
    raw = (
        os.environ.get("MEDIMAGE_DESKTOP_BACKEND_PORT")
        or os.environ.get("MEDIMAGE_BACKEND_PORT")
        or str(DEFAULT_DESKTOP_PORT)
    )
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid desktop backend port: {raw}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"Desktop backend port out of range: {port}")
    return port


def parse_args(argv: Sequence[str] | None = None) -> DesktopBackendConfig:
    parser = argparse.ArgumentParser(description="Start the MedImage Agent desktop backend.")
    parser.add_argument(
        "--host",
        default=os.environ.get("MEDIMAGE_DESKTOP_BACKEND_HOST", DEFAULT_DESKTOP_HOST),
        help="Backend bind host. Desktop mode only permits 127.0.0.1.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_env_port(),
        help="Backend bind port. Defaults to MEDIMAGE_DESKTOP_BACKEND_PORT or 8765.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("MEDIMAGE_DESKTOP_BACKEND_LOG_LEVEL", "info"),
        choices=("critical", "error", "warning", "info", "debug", "trace"),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise ValueError(f"Desktop backend port out of range: {args.port}")
    return DesktopBackendConfig(
        host=validate_host(args.host),
        port=args.port,
        log_level=args.log_level,
    )


def run_backend(config: DesktopBackendConfig) -> None:
    ensure_packaged_windows_runtime_dirs()
    start_parent_watchdog()
    os.environ.setdefault("MEDIMAGE_DESKTOP", "1")
    os.environ["MEDIMAGE_DESKTOP_BACKEND_HOST"] = config.host
    os.environ["MEDIMAGE_DESKTOP_BACKEND_PORT"] = str(config.port)
    uvicorn.run(
        APP_IMPORT_STRING,
        host=config.host,
        port=config.port,
        reload=False,
        factory=False,
        log_level=config.log_level,
    )


def _sandbox_self_test_argv(
    case_id: str, memory_input_path: Path
) -> tuple[str, ...]:
    """Build a fixed Windows helper invocation shared by source and packages."""
    # This helper is unit-tested on non-Windows CI hosts.  Keep Windows path
    # semantics explicit so a POSIX Path does not reinterpret ``C:\Windows``
    # as a relative path beneath the checkout or pytest temporary directory.
    system32 = PureWindowsPath(
        os.environ.get("SystemRoot", r"C:\Windows")
    ) / "System32"

    def helper_path(name: str) -> str:
        executable = system32 / name
        # Preserve canonicalization on the real Windows runtime while keeping
        # path construction deterministic when Linux CI exercises the helper.
        return str(Path(executable).resolve()) if os.name == "nt" else str(executable)

    if case_id == "timeout":
        return (helper_path("cmd.exe"), "/d", "/q", "/c", "for /l %i in (1,1,2147483647) do @rem")
    if case_id == "memory_limit":
        return (helper_path("sort.exe"), str(memory_input_path))
    executable = helper_path("cmd.exe")
    command = _SANDBOX_SELF_TEST_COMMANDS.get(case_id)
    if case_id == "spawn_child_tree":
        # cmd.exe parses /c text itself, not with the C runtime quoting rules
        # used by list2cmdline. Keep this fixed command free of embedded quotes
        # and resolve the nested helper from the request's System32-only PATH.
        command = (
            "cd /d staged_input & cmd.exe /d /q /c echo "
            f"{_SANDBOX_CHILD_STARTED_MARKER} & exit /b 0"
        )
    if command is None:
        raise ValueError("Unknown sandbox self-test case")
    return (
        executable,
        "/d",
        "/q",
        "/c",
        command,
    )


def _run_sandbox_self_test_process(request, *, timeout_seconds: int):
    from src.backend.app.runtime.sandbox_process_runner import SandboxProcessRunner

    return SandboxProcessRunner().run(request, timeout_seconds=timeout_seconds)


def _sandbox_self_test_output_contains(path: str, marker: str) -> bool:
    try:
        return marker.encode("ascii") in Path(path).read_bytes()
    except OSError:
        return False


def _sandbox_network_self_test(case_id: str, request):
    """Compare the fixed Windows curl probe with a reachable host-owned server.

    No Internet, credentials, arbitrary endpoint or production API is involved.
    Windows Filtering Platform can reject or silently drop the connection,
    producing curl 7 or 28 respectively. A live control before AND after,
    no accepted sandbox connection, and the verified child token are all
    required; process timeouts and loader failures are never accepted.
    """
    from src.backend.app.core.exceptions import SafetyError

    ipv6 = case_id == "network_loopback_ipv6"
    address = "::1" if ipv6 else "127.0.0.1"
    if case_id == "network_host_ipv4":
        address = socket.gethostbyname(socket.gethostname())
        if address.startswith("127.") or address == "0.0.0.0":
            raise SafetyError("SANDBOX_NETWORK_CONTROL_UNAVAILABLE", code="SANDBOX_NETWORK_CONTROL_UNAVAILABLE")
    stop = threading.Event()
    received = threading.Event()
    control_received = threading.Event()
    response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
    with socket.socket(socket.AF_INET6 if ipv6 else socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((address, 0))
        server.listen(4)
        server.settimeout(0.1)
        port = server.getsockname()[1]

        def serve():
            while not stop.is_set():
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    if control_received.is_set():
                        received.set()
                    connection.sendall(response)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            with socket.create_connection((address, port), timeout=2) as control:
                host_allowed = control.recv(4096) == response
            control_received.set()
            executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe")
            url = f"http://[{address}]:{port}/" if ipv6 else f"http://{address}:{port}/"
            probe = request.model_copy(update={
                "executable_path": executable,
                "argv": (executable, "--noproxy", "*", "--silent", "--show-error", "--connect-timeout", "2", "--max-time", "3", url),
            })
            result = _run_sandbox_self_test_process(probe, timeout_seconds=request.timeout_seconds)
            sandbox_denied = result.status == "FAILED" and result.return_code in {7, 28} and not received.is_set()
            control_received.clear()
            with socket.create_connection((address, port), timeout=2) as control:
                host_allowed = host_allowed and control.recv(4096) == response
            return result, {"host_allowed": host_allowed, "sandbox_denied": sandbox_denied}
        finally:
            stop.set()
            worker.join(timeout=3)
            if worker.is_alive():
                raise SafetyError("SANDBOX_NETWORK_CONTROL_CLEANUP_FAILED", code="SANDBOX_NETWORK_CONTROL_CLEANUP_FAILED")


def run_sandbox_self_test(case_id: str) -> int:
    """Execute a fixed sandbox probe and emit only a redacted JSON result."""
    if case_id not in _SANDBOX_SELF_TEST_CASES or not _is_windows_runtime():
        print(json.dumps({"ok": False, "code": "SANDBOX_PROVIDER_UNAVAILABLE"}))
        return 2
    from src.backend.app.schemas.sandbox import SandboxProcessRequest
    from tempfile import TemporaryDirectory

    # The temporary directory is intentionally a direct child of the current
    # workspace and is removed after every probe; callers cannot select it.
    with TemporaryDirectory(prefix=".sandbox-self-test-", dir=Path.cwd()) as root_value:
        root = Path(root_value).resolve()
        work = (root / "work").resolve()
        if work.parent != root:
            print(json.dumps({"ok": False, "code": "SANDBOX_SELF_TEST_INVALID"}))
            return 2
        (root / "rawdata").mkdir(parents=True, exist_ok=True)
        work.mkdir(parents=True, exist_ok=True)
        (work / "tmp").mkdir(exist_ok=True)
        (work / "output").mkdir(exist_ok=True)
        (work / "staged_input").mkdir(exist_ok=True)
        memory_input_path = work / "staged_input" / "memory-input.txt"
        if case_id == "memory_limit":
            with memory_input_path.open("wb") as stream:
                chunk = (b"z" * 1022) + b"\r\n"
                for _ in range(32 * 1024):
                    stream.write(chunk)
        timeout = 1 if case_id == "timeout" else 15
        argv = _sandbox_self_test_argv("write_allowed_output" if case_id.startswith("network_") else case_id, memory_input_path)
        environment = {
            "TEMP": str(work / "tmp"),
            "TMP": str(work / "tmp"),
            "LOCALAPPDATA": str(work / "tmp"),
            "SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
        }
        if case_id == "spawn_child_tree":
            environment["PATH"] = str(
                PureWindowsPath(environment["SystemRoot"]) / "System32"
            )
        request = SandboxProcessRequest(
            sandbox_id=f"selftest-{case_id}", executable_path=argv[0],
            argv=argv, cwd=str(work), environment=environment,
            policy_hash="sandbox-self-test-v1", timeout_seconds=timeout,
            memory_limit_bytes=(16 if case_id == "memory_limit" else 64) * 1024 * 1024,
            max_processes=1,
        )
        try:
            control_result = None
            control_child_started = None
            network_probe = None
            if case_id == "spawn_child_tree":
                control_request = request.model_copy(update={
                    "sandbox_id": "selftest-spawn-child-control",
                    "max_processes": 2,
                })
                control_result = _run_sandbox_self_test_process(
                    control_request, timeout_seconds=timeout
                )
                control_child_started = _sandbox_self_test_output_contains(
                    control_result.stdout_path, _SANDBOX_CHILD_STARTED_MARKER
                )
            if case_id == "memory_limit":
                control_request = request.model_copy(update={
                    "sandbox_id": "selftest-memory-limit-control",
                    "memory_limit_bytes": 256 * 1024 * 1024,
                })
                control_result = _run_sandbox_self_test_process(
                    control_request, timeout_seconds=timeout
                )
            if case_id.startswith("network_"):
                result, network_probe = _sandbox_network_self_test(case_id, request)
            else:
                result = _run_sandbox_self_test_process(request, timeout_seconds=timeout)
        except Exception as exc:
            details = getattr(exc, "details", {})
            payload: dict[str, object] = {
                "ok": False,
                "code": getattr(exc, "code", "SANDBOX_PROCESS_START_FAILED"),
            }
            if isinstance(details, dict):
                stage = details.get("stage")
                winerror = details.get("winerror")
                if isinstance(stage, str) and stage:
                    payload["stage"] = stage
                if isinstance(winerror, int) and winerror >= 0:
                    payload["winerror"] = winerror
            print(json.dumps(payload))
            return 1
        expected_status = (
            "TIMED_OUT" if case_id == "timeout"
            else "FAILED" if case_id == "memory_limit"
            else "SUCCEEDED"
        )
        ok = result.status == expected_status
        process_tree_probe = None
        if case_id == "spawn_child_tree":
            limited_child_started = _sandbox_self_test_output_contains(
                result.stdout_path, _SANDBOX_CHILD_STARTED_MARKER
            )
            process_tree_probe = {
                "control_child_started": control_child_started is True,
                "limit_one_blocked_child": not limited_child_started,
            }
            ok = (
                control_result is not None
                and control_result.status == "SUCCEEDED"
                and control_result.network_isolation == "enforced"
                and control_child_started is True
                and result.status == "SUCCEEDED"
                and result.return_code == 0
                and result.network_isolation == "enforced"
                and not limited_child_started
            )
        if network_probe is not None:
            ok = all(network_probe.values()) and result.network_isolation == "enforced"
        if case_id == "memory_limit":
            ok = (
                control_result is not None
                and control_result.status == "SUCCEEDED"
                and result.return_code == 1
            )
        if case_id == "write_allowed_output":
            proof_path = work / "output" / "proof.txt"
            ok = ok and proof_path.is_file() and proof_path.read_text(
                encoding="ascii"
            ).strip() == "ok"
        payload = {
            "ok": ok,
            "code": result.status,
            "return_code": result.return_code,
            "network_isolation": result.network_isolation if ok else "unverified",
            "network_probe": network_probe,
        }
        if process_tree_probe is not None:
            payload["process_tree_probe"] = process_tree_probe
        print(json.dumps(payload))
        return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--sandbox-self-test":
        return run_sandbox_self_test(args[1])
    config = parse_args(args)
    run_backend(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
