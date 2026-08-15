"""Retry dry-run and fail-closed execution compatibility routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.api.models import RetryDryRunRequest, RetryExecuteRequest
from src.backend.app.runtime.retry_runtime import dry_run_retry_plan

router = APIRouter(tags=["retry"])


def _require_safe_retry_run_id(run_id: str) -> None:
    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id.")


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.post("/api/retry/dry-run")
def api_retry_dry_run(payload: RetryDryRunRequest) -> dict[str, Any]:
    _require_safe_retry_run_id(payload.run_id)
    return dry_run_retry_plan(run_id=payload.run_id, retry_run_id=payload.retry_run_id)


@router.post("/api/retry/execute")
def api_retry_execute(payload: RetryExecuteRequest) -> dict[str, Any]:
    _require_safe_retry_run_id(payload.run_id)
    reject_execution_contract("retry.execute")


@router.get("/api/retry-runs/{retry_run_id}")
def api_get_retry_run(retry_run_id: str) -> dict[str, Any]:
    _require_safe_retry_run_id(retry_run_id)
    base = Path("outputs/work") / "retry_runs" / retry_run_id
    return {
        "ok": True,
        "retry_run_id": retry_run_id,
        "dry_run_summary": _read_json_if_exists(base / "dry_run_summary.json"),
        "retry_execution_summary": _read_json_if_exists(base / "retry_execution_summary.json"),
    }
