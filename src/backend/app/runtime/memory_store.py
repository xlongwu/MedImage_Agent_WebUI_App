"""Deprecated run-history compatibility store.

This module is not the project-scoped long-term Memory Domain and must not be
used by planner or execution code as a memory authority. New long-term memory
code uses ``services.memory_repository``. These wrappers remain only for
tracked run-history/background-review compatibility until those consumers move
to the project-history domain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.app.runtime.memory_provider import MemoryProvider
from src.backend.app.runtime.memory_providers.file_provider import FileMemoryProvider  # noqa: F401

_default_provider: MemoryProvider | None = None


def get_provider() -> MemoryProvider:
    global _default_provider
    if _default_provider is None:
        _default_provider = FileMemoryProvider()
        _default_provider.initialize()
    return _default_provider


def set_provider(provider: MemoryProvider) -> None:
    global _default_provider
    if _default_provider:
        _default_provider.shutdown()
    _default_provider = provider
    provider.initialize()


# --- Backward-compatible wrappers ---

def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("Missing dependency: PyYAML. Install with: pip install pyyaml") from exc
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def ensure_memory_layout(root_dir: str = ".") -> dict[str, str]:
    # Ensure provider uses correct root
    provider = get_provider()
    if isinstance(provider, FileMemoryProvider):
        provider.root_dir = Path(root_dir)
    provider.initialize()
    return {
        "global_dir": str(Path(root_dir) / "memory" / "global"),
        "projects_dir": str(Path(root_dir) / "memory" / "projects"),
        "sessions_dir": str(Path(root_dir) / "memory" / "sessions"),
    }


def sanitize_project_name(project_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in project_name)
    return safe or "default_project"


def get_project_memory_dir(project_name: str, root_dir: str = ".") -> Path:
    ensure_memory_layout(root_dir)
    project_dir = Path(root_dir) / "memory" / "projects" / sanitize_project_name(project_name)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def append_run_history(
    project_name: str,
    record: dict[str, Any],
    root_dir: str = ".",
) -> Path:
    get_provider().append_event(project_name, record)
    return get_project_memory_dir(project_name, root_dir) / "RUN_HISTORY.jsonl"


def read_error_kb(root_dir: str = ".") -> dict[str, Any]:
    ensure_memory_layout(root_dir)
    return _load_yaml(Path(root_dir) / "src" / "backend" / "app" / "resources" / "error_kb.yaml")


def match_error_patterns(
    errors: list[str],
    root_dir: str = ".",
) -> list[dict[str, Any]]:
    kb = read_error_kb(root_dir)
    categories = kb.get("categories", {}) or {}
    joined_errors = "\n".join(errors)

    matches: list[dict[str, Any]] = []
    for category, cat_def in categories.items():
        for pattern in cat_def.get("patterns", []) or []:
            pattern = str(pattern)
            if pattern and pattern.lower() in joined_errors.lower():
                matches.append({
                    "category": category,
                    "pattern": pattern,
                    "severity": cat_def.get("severity", "unknown"),
                    "retryable": bool(cat_def.get("retryable", False)),
                    "suggested_fixes": list(cat_def.get("suggested_fixes", []) or []),
                })
                break

    return matches
