"""Experiment tracking, artifact browsing, and insights route handlers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.app.api._shared import _read_json_if_exists, _read_text_if_exists
from src.backend.app.api.models import ExperimentCompareRequest, ExperimentTrackingRequest

router = APIRouter()


@router.get("/api/experiments/run-index")
def api_experiments_run_index() -> dict[str, Any]:
    from src.backend.app.tools.experiment_tracker import build_run_index

    result = build_run_index("./work", "./reports")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result

@router.post("/api/experiments/record")
def api_experiments_create_record(
    request: ExperimentTrackingRequest,
) -> dict[str, Any]:
    from src.backend.app.tools.experiment_tracker import create_experiment_record

    result = create_experiment_record(
        experiment_id=request.experiment_id,
        name=request.name,
        run_ids=request.run_ids,
        tags=request.tags,
        notes=request.notes,
        work_dir="./work",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result

@router.post("/api/experiments/compare")
def api_experiments_compare(
    request: ExperimentCompareRequest,
) -> dict[str, Any]:
    from src.backend.app.tools.experiment_tracker import compare_experiment_runs

    result = compare_experiment_runs(
        experiment_id=request.experiment_id,
        run_ids=request.run_ids,
        work_dir="./work",
        report_dir="./reports",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result

@router.get("/api/experiments/record/{experiment_id}")
def api_experiments_get_record(experiment_id: str) -> dict[str, Any]:
    path = Path("outputs/work") / "experiments" / "records" / f"{experiment_id}.json"
    data = _read_json_if_exists(path)
    if data is None:
        raise HTTPException(status_code=404, detail="Experiment record not found")
    return {"ok": True, "record": data}

@router.get("/api/experiments/comparison/{experiment_id}")
def api_experiments_get_comparison(experiment_id: str) -> dict[str, Any]:
    json_path = Path("outputs/reports") / "experiments" / f"{experiment_id}_comparison.json"
    md_path = Path("outputs/reports") / "experiments" / f"{experiment_id}_comparison_report.md"

    data = _read_json_if_exists(json_path)
    markdown = _read_text_if_exists(md_path)

    if data is None:
        raise HTTPException(status_code=404, detail="Comparison not found")

    return {
        "ok": True,
        "comparison": data,
        "markdown": markdown,
    }

@router.get("/api/experiments/dashboard")
def api_get_experiment_dashboard() -> dict[str, Any]:
    from src.backend.app.tools.experiment_dashboard import build_experiment_dashboard

    base = Path("outputs/work") / "experiments"
    report_base = Path("outputs/reports") / "experiments"

    dashboard = _read_json_if_exists(base / "dashboard_data.json")
    dashboard_csv = _read_text_if_exists(base / "dashboard_data.csv")
    dashboard_report = _read_text_if_exists(report_base / "dashboard_report.md")

    if dashboard is None:
        dashboard = build_experiment_dashboard(
            work_dir="./work",
            report_dir="./reports",
            refresh_index=True,
        )

    return {
        "ok": True,
        "dashboard": dashboard,
        "dashboard_csv": dashboard_csv,
        "dashboard_report": dashboard_report,
    }

@router.post("/api/experiments/dashboard/refresh")
def api_refresh_experiment_dashboard() -> dict[str, Any]:
    from src.backend.app.tools.experiment_dashboard import build_experiment_dashboard

    result = build_experiment_dashboard(
        work_dir="./work",
        report_dir="./reports",
        refresh_index=True,
    )

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result

@router.get("/api/artifacts")
def api_get_artifacts() -> dict[str, Any]:
    from src.backend.app.tools.artifact_browser import build_artifact_index

    index_path = Path("outputs/work") / "artifacts" / "artifact_index.json"

    index = _read_json_if_exists(index_path)
    if index is None:
        index = build_artifact_index()

    return {
        "ok": True,
        "index": index,
        "markdown": _read_text_if_exists(
            Path("outputs/reports") / "artifacts" / "artifact_index.md"
        ),
    }

@router.get("/api/artifacts/preview")
def api_get_artifact_preview(path: str = Query(...), max_lines: int = Query(80)) -> dict[str, Any]:
    from src.backend.app.tools.artifact_browser import preview_artifact

    result = preview_artifact(path=path, max_lines=max_lines)

    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)

    return result

@router.get("/api/insights")
def api_get_insights() -> dict[str, Any]:
    from src.backend.app.tools.artifact_browser import build_artifact_index
    from src.backend.app.tools.insights import generate_insights_from_index

    base = Path("outputs/reports") / "insights"
    insights_json = _read_json_if_exists(base / "insights_summary.json")
    insights_md = _read_text_if_exists(base / "insights_report.md")

    # Always regenerate for freshness
    index_path = Path("outputs/work") / "artifacts" / "artifact_index.json"
    index = _read_json_if_exists(index_path)
    if index is None:
        index = build_artifact_index()

    insights = generate_insights_from_index(
        artifact_index=index,
        report_dir="./reports",
    )

    return {
        "ok": True,
        "insights": insights,
        "insights_json": insights_json,
        "insights_md": insights_md,
    }
