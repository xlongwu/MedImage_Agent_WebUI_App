"""Launch the frozen backend and verify packaged scientific capabilities."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

if os.name == "nt":
    from ctypes import wintypes


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, *, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {exc.code}: {body}") from exc


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def _wait_for_port_closed(port: int, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _port_is_open(port):
            return True
        time.sleep(0.2)
    return not _port_is_open(port)


def _windows_process_image(pid: int) -> Path | None:
    if os.name != "nt":
        return None
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            buffer,
            ctypes.byref(size),
        ):
            return None
        return Path(buffer.value).resolve()
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _windows_listener_owner(port: int) -> tuple[int, Path | None] | None:
    if os.name != "nt":
        return None

    class TcpRowOwnerPid(ctypes.Structure):
        _fields_ = [
            ("state", wintypes.DWORD),
            ("local_address", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_address", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("owning_pid", wintypes.DWORD),
        ]

    af_inet = 2
    tcp_table_owner_pid_listener = 3
    insufficient_buffer = 122
    size = wintypes.DWORD(0)
    status = ctypes.windll.iphlpapi.GetExtendedTcpTable(
        None,
        ctypes.byref(size),
        True,
        af_inet,
        tcp_table_owner_pid_listener,
        0,
    )
    if status != insufficient_buffer:
        return None
    table = ctypes.create_string_buffer(size.value)
    status = ctypes.windll.iphlpapi.GetExtendedTcpTable(
        table,
        ctypes.byref(size),
        True,
        af_inet,
        tcp_table_owner_pid_listener,
        0,
    )
    if status != 0:
        return None
    count = wintypes.DWORD.from_buffer_copy(table.raw[:4]).value
    row_size = ctypes.sizeof(TcpRowOwnerPid)
    for index in range(count):
        offset = 4 + (index * row_size)
        row = TcpRowOwnerPid.from_buffer_copy(table.raw[offset : offset + row_size])
        if socket.ntohs(row.local_port & 0xFFFF) == port:
            pid = int(row.owning_pid)
            return pid, _windows_process_image(pid)
    return None


def _stop_backend_process_tree(
    proc: subprocess.Popen[bytes],
    *,
    port: int,
    backend: Path,
) -> None:
    """Stop the PyInstaller bootloader and its extracted backend child."""
    if os.name == "nt" and proc.poll() is None:
        subprocess.run(
            ["taskkill", "/pid", str(proc.pid), "/t", "/f"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=30)
    if _wait_for_port_closed(port):
        return

    if os.name == "nt":
        owner = _windows_listener_owner(port)
        if owner is not None:
            listener_pid, listener_image = owner
            expected_image = backend.resolve()
            if listener_image is None or listener_image != expected_image:
                actual = str(listener_image) if listener_image else "unavailable"
                raise RuntimeError(
                    "Probe port remained open but its owner did not match the built "
                    f"backend (pid={listener_pid}, image={actual})"
                )
            subprocess.run(
                ["taskkill", "/pid", str(listener_pid), "/t", "/f"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if _wait_for_port_closed(port):
                return

    raise RuntimeError(
        "Frozen backend process tree still owns its probe port after verified cleanup"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backend_exe", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("result_path", type=Path)
    parser.add_argument("--expect-cupy", action="store_true")
    args = parser.parse_args()

    backend = args.backend_exe.resolve()
    workspace = args.workspace.resolve()
    result_path = args.result_path.resolve()
    if not backend.is_file():
        raise FileNotFoundError(backend)
    workspace.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "MEDIMAGE_DESKTOP": "1",
            "MEDIMAGE_DESKTOP_WORKSPACE": str(workspace),
            "CUPY_CACHE_DIR": str(workspace / "cupy-cache"),
        }
    )
    stdout_path = workspace / "backend.stdout.log"
    stderr_path = workspace / "backend.stderr.log"
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        proc = subprocess.Popen(
            [str(backend), "--host", "127.0.0.1", "--port", str(port)],
            cwd=workspace,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"Frozen backend exited before health check: {proc.returncode}"
                    )
                try:
                    _get_json(f"{base}/api/health", timeout=2)
                    break
                except (OSError, urllib.error.URLError, TimeoutError):
                    time.sleep(1)
            else:
                raise TimeoutError(
                    "Frozen backend did not become healthy within 180 seconds"
                )

            gpu = _get_json(f"{base}/api/gpu/detect", timeout=120)
            dicom = _get_json(
                f"{base}/api/desktop/capabilities/dicom-conversion",
                timeout=30,
            )
            dicom_capability = dict(dicom.get("capability") or {})
            evidence = {
                "ok": True,
                "backend_exe": backend.name,
                "expect_cupy": bool(args.expect_cupy),
                "cupy_available": bool(gpu.get("cupy_available")),
                "gpu_available": bool(gpu.get("gpu_available")),
                "capability_error_code": gpu.get("capability_error_code"),
                "warnings": list(gpu.get("warnings") or []),
                "dicom_converter_available": bool(
                    dicom_capability.get("converter_available")
                ),
                "dicom_execution_supported": bool(
                    dicom_capability.get("execution_supported")
                ),
                "dicom_converter_name": dicom_capability.get("converter_name"),
                "dicom_converter_version": dicom_capability.get("converter_version"),
                "dicom_error": dicom_capability.get("error"),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            }
            if not (
                evidence["dicom_converter_available"]
                and evidence["dicom_execution_supported"]
            ):
                evidence["ok"] = False
                result_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                raise RuntimeError(
                    "Frozen backend DICOM converter is unavailable: "
                    + str(evidence["dicom_error"])
                )
            if args.expect_cupy and not evidence["cupy_available"]:
                evidence["ok"] = False
                result_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                raise RuntimeError(
                    "Frozen backend could not import CuPy: "
                    + " | ".join(evidence["warnings"])
                )
            result_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(json.dumps(evidence, indent=2))
        finally:
            _stop_backend_process_tree(proc, port=port, backend=backend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
