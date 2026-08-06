from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

DESKTOP_CONFIG_PATH = Path("outputs/work/desktop/config.json")


DEFAULT_DESKTOP_CONFIG: dict[str, Any] = {
    "project_dir": ".",
    "active_project_id": "",
    "recent_projects": [],
    "authorized_data_dirs": [],
    "python_path": sys.executable,
    "matlab_command": "matlab",
    "spm_dir": "./third_party/spm12",
    "dpabi_dir": "./third_party/DPABI_V8.2_240510",
    "gpu_mode": "prefer",
    "llm": {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key_set": False,
    },
    "dicom_conversion": {
        "enabled": False,
        "dcm2niix_path": "",
        "prefer_bundled": True,
        "overwrite_policy": "fail_if_exists",
        "timeout_seconds": 1800,
    },
}


def _read_config() -> dict[str, Any]:
    if not DESKTOP_CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(DESKTOP_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_desktop_config(redacted: bool = True) -> dict[str, Any]:
    config = _merge(DEFAULT_DESKTOP_CONFIG, _read_config())
    # Do not expose the retired configuration key retained in older local files.
    config.pop("gui_agent", None)
    config["llm"] = _merge(
        config.get("llm", {}),
        {
            "enabled": os.environ.get("MEDIMAGE_LLM_ENABLED", str(config.get("llm", {}).get("enabled", False))).lower() == "true",
            "base_url": os.environ.get("MEDIMAGE_LLM_BASE_URL", config.get("llm", {}).get("base_url")),
            "model": os.environ.get("MEDIMAGE_LLM_MODEL", config.get("llm", {}).get("model", "")),
            "api_key_set": bool(os.environ.get("MEDIMAGE_LLM_API_KEY")) or bool(config.get("llm", {}).get("api_key_set")),
        },
    )
    if redacted:
        config["llm"].pop("api_key", None)
    return config


def save_desktop_config(payload: dict[str, Any]) -> dict[str, Any]:
    existing = get_desktop_config(redacted=False)
    existing.pop("gui_agent", None)
    clean = dict(payload)
    clean.pop("gui_agent", None)
    llm = dict(clean.get("llm", {}))
    if llm.get("api_key"):
        llm["api_key_set"] = True
        llm.pop("api_key", None)
    clean["llm"] = llm
    saved = _merge(existing, clean)
    DESKTOP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DESKTOP_CONFIG_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "config": get_desktop_config(redacted=True), "config_path": str(DESKTOP_CONFIG_PATH)}


def _path_check(name: str, path: str) -> dict[str, Any]:
    p = Path(path)
    return {
        "name": name,
        "ok": p.exists(),
        "path": str(p),
        "resolved": str(p.resolve()) if p.exists() else "",
    }


def _command_check(name: str, command: str) -> dict[str, Any]:
    found = shutil.which(command)
    path_exists = Path(command).exists()
    return {"name": name, "ok": bool(found or path_exists), "command": command, "resolved": found or (str(Path(command).resolve()) if path_exists else "")}


def get_desktop_health() -> dict[str, Any]:
    config = get_desktop_config()
    from src.backend.app.services.environment_health import build_matlab_spm_health  # noqa: E402

    matlab_spm = build_matlab_spm_health()
    checks = [
        _path_check("project_dir", config.get("project_dir", ".")),
        _path_check("python_path", config.get("python_path", sys.executable)),
        _command_check("matlab_command", config.get("matlab_command", "matlab")),
        _path_check("spm_dir", config.get("spm_dir", "./third_party/spm12")),
        _path_check("dpabi_dir", config.get("dpabi_dir", "./third_party/DPABI_V8.2_240510")),
    ]

    try:
        from src.backend.app.tools.gpu_utils import detect_gpu

        gpu = detect_gpu()
    except Exception as exc:
        gpu = {"ok": False, "gpu_available": False, "errors": [str(exc)]}

    checks.append({"name": "llm_config", "ok": bool(config.get("llm", {}).get("api_key_set")) or not config.get("llm", {}).get("enabled"), "enabled": config.get("llm", {}).get("enabled"), "api_key_set": config.get("llm", {}).get("api_key_set")})
    checks.append(_websocket_runtime_check())
    checks.append(_desktop_store_check())
    checks.append(_pipeline_adapters_check())

    return {
        "ok": True,
        "config": config,
        "checks": checks,
        "all_required_ok": all(item.get("ok", False) for item in checks if item["name"] in {"project_dir", "python_path"}),
        "gpu": gpu,
        "matlab_spm": matlab_spm,
    }


def add_recent_project(project_id: str, project_name: str, project_dir: str) -> None:
    """Add or update a project in the desktop config recent_projects list."""
    config = get_desktop_config(redacted=False)
    raw_recent = config.get("recent_projects", [])
    recent = [item for item in raw_recent if isinstance(item, dict)]
    resolved_project_dir = str(Path(project_dir).expanduser().resolve())
    normalized_name = project_name.strip().casefold()
    recent = [
        item
        for item in recent
        if item.get("project_id") != project_id
        and str(item.get("project_name", "")).strip().casefold() != normalized_name
        and str(item.get("project_dir", "")).casefold()
        != resolved_project_dir.casefold()
    ]
    recent.insert(0, {
        "project_id": project_id,
        "project_name": project_name,
        "project_dir": resolved_project_dir,
    })
    save_desktop_config({"recent_projects": recent[:20]})


def remove_recent_project(project_id: str) -> bool:
    """Remove a project from recent_projects without touching project files."""
    config = get_desktop_config(redacted=False)
    raw_recent = config.get("recent_projects", [])
    recent = [item for item in raw_recent if isinstance(item, dict)]
    next_recent = [item for item in recent if item.get("project_id") != project_id]
    removed = len(next_recent) != len(recent)
    payload: dict[str, Any] = {"recent_projects": next_recent}
    if config.get("active_project_id") == project_id:
        next_project = next_recent[0] if next_recent else {}
        payload["active_project_id"] = str(next_project.get("project_id") or "")
        payload["project_dir"] = str(
            next_project.get("project_dir") or DEFAULT_DESKTOP_CONFIG["project_dir"]
        )
    save_desktop_config(payload)
    return removed


def set_active_project(project_id: str, project_dir: str | None = None) -> None:
    """Update the active project in desktop config."""
    payload = {"active_project_id": project_id}
    if project_dir:
        payload["project_dir"] = str(Path(project_dir).expanduser().resolve())
    save_desktop_config(payload)


def add_authorized_data_dir(path: str) -> None:
    """Add a user-authorized data directory to the allow list."""
    config = get_desktop_config(redacted=False)
    raw_dirs = config.get("authorized_data_dirs", [])
    dirs: list[dict[str, str]] = []
    for item in raw_dirs if isinstance(raw_dirs, list) else []:
        if isinstance(item, dict) and item.get("path"):
            dirs.append(item)
        elif isinstance(item, str):
            dirs.append({"path": item})

    resolved_path = str(Path(path).expanduser().resolve())
    if not any(
        str(item.get("path", "")).casefold() == resolved_path.casefold()
        for item in dirs
    ):
        dirs.append({
            "path": resolved_path,
            "authorized_at": datetime.now(UTC).isoformat(),
            "authorized_by": "user-selection",
            "scope": "read-only",
        })
    save_desktop_config({"authorized_data_dirs": dirs})


def _websocket_runtime_check() -> dict[str, Any]:
    installed = [name for name in ("websockets", "wsproto") if find_spec(name)]
    return {
        "name": "websocket_runtime",
        "ok": bool(installed),
        "installed": installed,
        "detail": "uvicorn WebSocket transport available" if installed else "Install uvicorn[standard] or websockets for live task streams.",
    }


def _desktop_store_check() -> dict[str, Any]:
    try:
        from src.backend.app.services.mock_store import mock_store

        return mock_store.health_check()
    except Exception as exc:
        return {"name": "desktop_store", "ok": False, "error": str(exc)}


def _pipeline_adapters_check() -> dict[str, Any]:
    adapters = {
        "simulated": True,
        "external_smoke": False,
        "rsfmri_python": False,
    }
    try:
        from src.backend.app.tools.external_smoke import run_external_smoke  # noqa: F401

        adapters["external_smoke"] = True
    except Exception:
        adapters["external_smoke"] = False
    try:
        from src.backend.app.tools.run_quickstart_demo_cli import main  # noqa: F401

        adapters["rsfmri_python"] = True
    except Exception:
        adapters["rsfmri_python"] = False
    return {"name": "pipeline_adapters", "ok": any(adapters.values()), "adapters": adapters}


def get_dicom_conversion_capability() -> dict[str, Any]:
    """Return DICOM conversion capability info for the Settings page and capability API.

    Per 实现dcm2nii任务方案.md §10.2, returns:
      enabled, converter_available, converter_name, converter_path,
      converter_version, converter_sha256, execution_supported.
    """
    config = get_desktop_config(redacted=True)
    dc = config.get("dicom_conversion", {}) or {}

    try:
        from src.backend.app.services.dicom_conversion_execution import (
            check_native_dicom_converter_availability,
        )
        detection = check_native_dicom_converter_availability()
        available = bool(detection.get("found"))
        path = None
        version = detection.get("version")
        sha256 = None
        strategy = "in_project_python"
        error = detection.get("error")
    except Exception as exc:
        available = False
        path = None
        version = None
        sha256 = None
        strategy = "none"
        error = f"Native DICOM converter dependency check failed: {exc}"

    return {
        "enabled": bool(dc.get("enabled", False)),
        "converter_available": available,
        "converter_name": "medimage-native",
        "converter_path": path,
        "converter_version": version,
        "converter_sha256": sha256,
        "converter_strategy": strategy,
        "execution_supported": available,
        "prefer_bundled": bool(dc.get("prefer_bundled", True)),
        "overwrite_policy": dc.get("overwrite_policy", "fail_if_exists"),
        "timeout_seconds": dc.get("timeout_seconds", 1800),
        "error": error if not available else None,
    }
