"""Scheduler planning routes independent from the Agent Task domain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.models import SchedulerPlanRequest
from src.backend.app.config import ProjectSettings
from src.backend.app.core.exceptions import ConfigError
from src.backend.app.runtime.scheduler import create_scheduler_plan
from src.backend.app.schemas.pipeline_schema import load_pipeline_yaml

router = APIRouter(tags=["scheduler"])


def _load_project_config(path: str) -> dict[str, Any]:
    try:
        ProjectSettings.from_yaml(path)
    except FileNotFoundError as exc:
        raise ConfigError(str(exc)) from exc
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Project config not found: {path}")
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ConfigError(f"Failed to parse project config: {exc}") from exc


@router.post("/api/scheduler/plan")
def api_scheduler_plan(request: SchedulerPlanRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(request.project_config_path)
        pipeline = load_pipeline_yaml(request.pipeline_path)
        result = create_scheduler_plan(pipeline, project_config)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise_api_error(exc)
