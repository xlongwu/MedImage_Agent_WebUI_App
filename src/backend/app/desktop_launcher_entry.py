from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.backend.app.main import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@dataclass(frozen=True)
class DesktopLauncherConfig:
    host: str
    port: int
    no_browser: bool
    no_window: bool


def validate_host(host: str) -> str:
    normalized = host.strip()
    if normalized != DEFAULT_HOST:
        raise ValueError("Desktop launcher host must be 127.0.0.1.")
    return normalized


def _resource_root() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[3]


def _local_app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def _find_repository_root(start: Path) -> Path | None:
    current = start.resolve()
    while True:
        if (current / "pyproject.toml").is_file() and (current / "desktop" / "electron").is_dir():
            return current
        if current.parent == current:
            return None
        current = current.parent


def _default_packaged_workspace(executable_dir: Path | None = None) -> Path:
    base = (executable_dir or Path(sys.executable).resolve().parent).resolve()
    repository_root = _find_repository_root(base)
    return (repository_root / "workspace") if repository_root else (base / "workspace")


def _copy_seed_dir(seed_root: Path, workspace: Path, name: str) -> None:
    source = seed_root / name
    target = workspace / name
    if source.exists() and not target.exists():
        shutil.copytree(source, target)


def prepare_workspace() -> Path:
    if hasattr(sys, "_MEIPASS"):
        workspace_env = os.environ.get("MEDIMAGE_DESKTOP_WORKSPACE")
        workspace = Path(workspace_env) if workspace_env else _default_packaged_workspace()
        seed_root = _resource_root() / "workspace_seed"
        workspace.mkdir(parents=True, exist_ok=True)
        for name in ("examples", "docs", "matlab"):
            _copy_seed_dir(seed_root, workspace, name)
        (workspace / "outputs").mkdir(parents=True, exist_ok=True)
        os.chdir(workspace)
        return workspace
    return Path.cwd()


def resolve_frontend_dir() -> Path:
    env_dir = os.environ.get("MEDIMAGE_DESKTOP_FRONTEND_DIR")
    if env_dir:
        return Path(env_dir)
    bundled = _resource_root() / "frontend"
    if bundled.exists():
        return bundled
    return Path(__file__).resolve().parents[3] / "src" / "frontend" / "dist"


def create_desktop_app(frontend_dir: Path | None = None) -> FastAPI:
    app = create_app()
    static_dir = frontend_dir or resolve_frontend_dir()
    if not (static_dir / "index.html").exists():
        raise FileNotFoundError(f"Frontend build not found: {static_dir}")
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="frontend")
    return app


def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(host: str, start_port: int) -> int:
    if start_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])
    for offset in range(40):
        port = start_port + offset
        if is_port_free(host, port):
            return port
    raise RuntimeError(f"No available launcher port from {start_port} to {start_port + 39}.")


def parse_args(argv: Sequence[str] | None = None) -> DesktopLauncherConfig:
    parser = argparse.ArgumentParser(description="Start MedImage Agent as a local desktop launcher.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MEDIMAGE_DESKTOP_BACKEND_PORT", DEFAULT_PORT)),
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-window", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        raise ValueError(f"Desktop launcher port out of range: {args.port}")
    return DesktopLauncherConfig(
        host=validate_host(args.host),
        port=args.port,
        no_browser=args.no_browser,
        no_window=args.no_window,
    )


def wait_for_health(base_url: str, attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=1.0) as response:
                if 200 <= response.status < 300:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.25)
    return False


def _run_status_window(base_url: str, shutdown) -> None:
    try:
        import tkinter as tk
    except Exception:
        print(f"MedImage Agent is running at {base_url}")
        print("Press Ctrl+C in this window to stop the backend.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            shutdown()
        return

    root = tk.Tk()
    root.title("MedImage Agent")
    root.geometry("440x190")
    root.resizable(False, False)
    root.configure(padx=18, pady=16)

    title = tk.Label(root, text="MedImage Agent is running locally", font=("Segoe UI", 12, "bold"))
    title.pack(anchor="w")
    tk.Label(root, text=base_url, font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 12))
    tk.Label(
        root,
        text="Close this window to stop the local FastAPI backend.",
        font=("Segoe UI", 9),
    ).pack(anchor="w", pady=(0, 12))

    buttons = tk.Frame(root)
    buttons.pack(anchor="e", fill="x")
    tk.Button(buttons, text="Open App", command=lambda: webbrowser.open(base_url)).pack(side="left")
    tk.Button(buttons, text="Quit", command=lambda: (shutdown(), root.destroy())).pack(side="right")
    root.protocol("WM_DELETE_WINDOW", lambda: (shutdown(), root.destroy()))
    root.mainloop()


def run_launcher(config: DesktopLauncherConfig) -> int:
    prepare_workspace()
    port = find_available_port(config.host, config.port)
    os.environ.setdefault("MEDIMAGE_DESKTOP", "1")
    os.environ["MEDIMAGE_DESKTOP_BACKEND_HOST"] = config.host
    os.environ["MEDIMAGE_DESKTOP_BACKEND_PORT"] = str(port)

    app = create_desktop_app()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=port,
            reload=False,
            log_level="info",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, name="medimage-desktop-backend", daemon=True)
    thread.start()

    base_url = f"http://{config.host}:{port}"
    if not wait_for_health(base_url):
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError(f"Desktop launcher backend health check timed out: {base_url}")

    if not config.no_browser:
        webbrowser.open(base_url)

    def shutdown() -> None:
        server.should_exit = True

    if config.no_window:
        try:
            while not server.should_exit:
                time.sleep(0.5)
        except KeyboardInterrupt:
            shutdown()
    else:
        _run_status_window(base_url, shutdown)

    server.should_exit = True
    thread.join(timeout=5)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    return run_launcher(config)


if __name__ == "__main__":
    raise SystemExit(main())
