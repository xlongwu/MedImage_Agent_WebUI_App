"""Shared utilities for API route modules.

Import this from any domain-specific route module to avoid
duplicating helpers like _load_project_config.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from src.backend.app.core.exceptions import ConfigError


def _read_json_if_exists(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text_if_exists(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _load_project_config(path: str) -> dict[str, Any]:
    """Load and validate a project config YAML file."""
    from src.backend.app.config import ProjectSettings

    try:
        ProjectSettings.from_yaml(path)
    except FileNotFoundError as exc:
        raise ConfigError(str(exc)) from exc
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Project config not found: {path}")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ConfigError(f"Failed to parse project config: {exc}") from exc
