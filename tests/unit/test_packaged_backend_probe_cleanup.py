from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "desktop" / "packaging" / "probe_backend_gpu_runtime.py"
SPEC = importlib.util.spec_from_file_location("probe_backend_gpu_runtime", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ExitedBootloader:
    def poll(self) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover - must not be called
        raise AssertionError("exited bootloader must not be killed")

    def wait(self, timeout: int) -> int:
        return 0


@pytest.mark.skipif(probe.os.name != "nt", reason="Windows process cleanup contract")
def test_cleanup_stops_verified_listener_after_bootloader_exits(monkeypatch, tmp_path):
    backend = (tmp_path / "medimage-backend.exe").resolve()
    backend.touch()
    waits = iter([False, True])
    calls: list[list[str]] = []

    monkeypatch.setattr(probe, "_wait_for_port_closed", lambda _port: next(waits))
    monkeypatch.setattr(
        probe,
        "_windows_listener_owner",
        lambda _port: (8123, backend),
    )
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv),
    )

    probe._stop_backend_process_tree(
        ExitedBootloader(),
        port=54321,
        backend=backend,
    )

    assert calls == [["taskkill", "/pid", "8123", "/t", "/f"]]


@pytest.mark.skipif(probe.os.name != "nt", reason="Windows process cleanup contract")
def test_cleanup_refuses_listener_owned_by_another_executable(monkeypatch, tmp_path):
    backend = (tmp_path / "medimage-backend.exe").resolve()
    other = (tmp_path / "other.exe").resolve()
    backend.touch()
    other.touch()
    calls: list[list[str]] = []

    monkeypatch.setattr(probe, "_wait_for_port_closed", lambda _port: False)
    monkeypatch.setattr(
        probe,
        "_windows_listener_owner",
        lambda _port: (9001, other),
    )
    monkeypatch.setattr(
        probe.subprocess,
        "run",
        lambda argv, **_kwargs: calls.append(argv),
    )

    with pytest.raises(RuntimeError, match="did not match the built backend"):
        probe._stop_backend_process_tree(
            ExitedBootloader(),
            port=54321,
            backend=backend,
        )

    assert calls == []
