"""Read-only legacy run inspection routes.

These routes retain the established ``/api/runs`` response contracts while
keeping run inspection independent from Agent Task planning.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.app.runtime.error_diagnoser import diagnose_run
from src.backend.app.runtime.run_inspector import (
    inspect_run,
    list_available_runs,
    read_state_detail,
)

router = APIRouter(tags=["runs"])


def _require_safe_run_id(run_id: str) -> None:
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id.")


@router.get("/api/runs")
def api_list_runs() -> dict[str, Any]:
    return list_available_runs("./work")


@router.get("/api/runs/{run_id}")
def api_inspect_run(run_id: str) -> dict[str, Any]:
    _require_safe_run_id(run_id)
    return inspect_run(run_id, "./work")


@router.get("/api/runs/{run_id}/state-detail")
def api_state_detail(run_id: str, path: str = Query(...)) -> dict[str, Any]:
    _require_safe_run_id(run_id)
    result = read_state_detail(run_id=run_id, state_path=path, work_dir="./work")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/api/runs/{run_id}/diagnosis")
def api_diagnose_run(run_id: str) -> dict[str, Any]:
    _require_safe_run_id(run_id)
    result = diagnose_run(run_id=run_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result
