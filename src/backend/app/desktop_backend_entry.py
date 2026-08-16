from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import uvicorn

DEFAULT_DESKTOP_HOST = "127.0.0.1"
DEFAULT_DESKTOP_PORT = 8765
APP_IMPORT_STRING = "src.backend.app.main:app"
DESKTOP_PARENT_PID_ENV = "MEDIMAGE_DESKTOP_PARENT_PID"
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SANDBOX_SELF_TEST_CASES = {
    "write_allowed_output", "write_rawdata_denied", "write_outside_project_denied",
    "spawn_child_tree", "memory_limit", "timeout", "print_environment_keys",
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


def _sandbox_self_test_worker(case_id: str) -> int:
    """Fixed internal probe; it receives no user command, path, or environment data."""
    root = Path.cwd().resolve()
    if case_id == "write_allowed_output":
        (root / "outputs").mkdir(exist_ok=True)
        (root / "outputs" / "proof.txt").write_text("ok", encoding="ascii")
        return 0
    if case_id in {"write_rawdata_denied", "write_outside_project_denied"}:
        target = root.parent / ("rawdata" if case_id == "write_rawdata_denied" else "outside") / "blocked.txt"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("blocked", encoding="ascii")
        except OSError:
            return 0
        return 1
    if case_id == "spawn_child_tree":
        import subprocess

        child = subprocess.Popen([sys.executable, "-c", "pass"])
        return 1 if child.poll() is None else 0
    if case_id == "memory_limit":
        _ = bytearray(256 * 1024 * 1024)
        return 1
    if case_id == "timeout":
        time.sleep(10)
        return 1
    if case_id == "print_environment_keys":
        sensitive = {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "COOKIE", "TOKEN"}
        return 1 if sensitive & {key.upper() for key in os.environ} else 0
    return 2


def run_sandbox_self_test(case_id: str) -> int:
    """Execute a fixed sandbox probe and emit only a redacted JSON result."""
    if case_id not in _SANDBOX_SELF_TEST_CASES or not _is_windows_runtime():
        print(json.dumps({"ok": False, "code": "SANDBOX_PROVIDER_UNAVAILABLE"}))
        return 2
    from src.backend.app.runtime.sandbox_process_runner import SandboxProcessRunner
    from src.backend.app.schemas.sandbox import SandboxProcessRequest

    root = (Path.cwd().resolve() / ".sandbox-self-test").resolve()
    work = (root / "work").resolve()
    if work.parent != root:
        print(json.dumps({"ok": False, "code": "SANDBOX_SELF_TEST_INVALID"}))
        return 2
    (root / "rawdata").mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    timeout = 1 if case_id == "timeout" else 15
    request = SandboxProcessRequest(
        sandbox_id=f"selftest-{case_id}", executable_path=sys.executable,
        argv=(sys.executable, "-m", "src.backend.app.desktop_backend_entry", "--sandbox-self-test-worker", case_id),
        cwd=str(work), environment={"TEMP": str(work / "tmp"), "TMP": str(work / "tmp")},
        policy_hash="sandbox-self-test-v1", timeout_seconds=timeout,
        memory_limit_bytes=64 * 1024 * 1024, max_processes=1,
    )
    try:
        result = SandboxProcessRunner().run(request, timeout_seconds=timeout)
    except Exception as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", "SANDBOX_PROCESS_START_FAILED")}))
        return 1
    expected_timeout = case_id == "timeout"
    ok = result.status == ("TIMED_OUT" if expected_timeout else "SUCCEEDED")
    print(json.dumps({"ok": ok, "code": result.status, "network_isolation": "not_enforced"}))
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--sandbox-self-test-worker":
        return _sandbox_self_test_worker(args[1])
    if len(args) == 2 and args[0] == "--sandbox-self-test":
        return run_sandbox_self_test(args[1])
    config = parse_args(args)
    run_backend(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
