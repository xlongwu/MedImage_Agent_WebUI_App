from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Keep module-level SQLiteDesktopStore instances created during pytest away
# from the persistent desktop database used by the local application.
_DESKTOP_STORE_ROOT = Path(tempfile.gettempdir()) / "medimage_agent_pytest"
_DESKTOP_STORE_ROOT.mkdir(parents=True, exist_ok=True)
_DESKTOP_STORE_PATH = _DESKTOP_STORE_ROOT / (f"desktop_state_{os.getpid()}_{uuid4().hex}.sqlite")
os.environ.setdefault(
    "MEDIMAGE_DESKTOP_STORE_PATH",
    str(_DESKTOP_STORE_PATH),
)
# Historical dashboard/API fixtures intentionally exercise the deterministic
# demo dataset. Production startup remains empty unless this explicit test-only
# opt-in is set.
os.environ.setdefault("MEDIMAGE_DESKTOP_SEED_DEMO_DATA", "true")
_CUPY_CACHE_ROOT = _DESKTOP_STORE_ROOT / f"cupy_cache_{os.getpid()}_{uuid4().hex}"
_CUPY_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("CUPY_CACHE_DIR", str(_CUPY_CACHE_ROOT))


@pytest.fixture(scope="session", autouse=True)
def cleanup_desktop_store() -> None:
    yield
    for suffix in ("", "-wal", "-shm"):
        Path(f"{_DESKTOP_STORE_PATH}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(_CUPY_CACHE_ROOT, ignore_errors=True)
    try:
        _DESKTOP_STORE_ROOT.rmdir()
    except OSError:
        pass


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


@pytest.fixture()
def clean_synthetic_dir(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic_bids" / "rawdata"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def desktop_store_factory(tmp_path: Path, monkeypatch):
    """Create independent desktop stores without touching application state."""

    from src.backend.app.services.mock_store import SQLiteDesktopStore

    monkeypatch.setenv("MEDIMAGE_DESKTOP_SEED_DEMO_DATA", "false")

    def create(name: str = "desktop_state") -> SQLiteDesktopStore:
        return SQLiteDesktopStore(tmp_path / f"{name}.sqlite")

    return create


@pytest.fixture(autouse=True)
def bind_project_store_dependency_to_test_store(monkeypatch) -> None:
    """Resolve route-facing store dependencies through the active test store.

    Historical fixtures patch ``project_routes.mock_store`` to an isolated
    SQLite instance. The production routes no longer expose one store global
    per domain module, so this test-only override keeps those fixtures isolated
    while exercising the same FastAPI dependency boundary as production.
    """

    from src.backend.app.api import project_routes
    from src.backend.app.api.dependencies import get_project_store
    from src.backend.app.main import app

    monkeypatch.setitem(
        app.dependency_overrides,
        get_project_store,
        lambda: project_routes.mock_store,
    )
