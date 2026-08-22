from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_dockerfiles_use_current_source_layout_and_locked_frontend_install() -> None:
    backend = _read("deploy/backend.Dockerfile")
    frontend = _read("deploy/frontend.Dockerfile")

    assert "COPY src/backend ./src/backend" in backend
    assert '"src.backend.app.main:app"' in backend
    assert "COPY backend ./backend" not in backend
    assert "COPY src/frontend/package*.json ./" in frontend
    assert "COPY src/frontend ./" in frontend
    assert "RUN npm ci" in frontend
    assert "COPY frontend ./" not in frontend


def test_local_profile_commands_match_current_source_layout() -> None:
    profile = yaml.safe_load(_read("deploy/local_profile.yaml"))

    assert profile["services"]["backend"]["command"].startswith(
        "uvicorn src.backend.app.main:app "
    )
    assert profile["services"]["frontend"]["command"] == (
        "cd src/frontend && npm run dev"
    )


def test_one_click_scripts_never_kill_port_owners() -> None:
    windows_script = _read("start.bat").casefold()
    posix_script = _read("start.sh").casefold()

    assert "taskkill" not in windows_script
    assert "kill -9" not in posix_script
    assert "taskkill" not in posix_script
    assert "already in use" in windows_script
    assert "already in use" in posix_script
    assert "/api/health" in windows_script
    assert "/api/health" in posix_script
