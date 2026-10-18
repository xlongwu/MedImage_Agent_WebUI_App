"""Pipeline, file, log, and dataset report route handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api._shared import _read_json_if_exists, _read_text_if_exists
from src.backend.app.runtime.path_safety import PathSafetyError, read_safe_text_file
from src.backend.app.schemas.pipeline_schema import load_pipeline_yaml

router = APIRouter()


@router.get("/api/pipelines")
def list_pipelines() -> dict[str, Any]:
    examples = Path("examples")
    pipelines = []
    for path in sorted(examples.glob("*.yaml")):
        pipelines.append(str(path))
    for path in sorted(examples.glob("*.yml")):
        pipelines.append(str(path))
    return {
        "ok": True,
        "pipelines": pipelines,
    }


@router.get("/api/pipelines/{pipeline_name}")
def get_pipeline(pipeline_name: str) -> dict[str, Any]:
    try:
        if "/" in pipeline_name or "\\" in pipeline_name or ".." in pipeline_name:
            raise ValueError("Invalid pipeline name.")

        path = Path("examples") / pipeline_name
        if path.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("Pipeline must be a YAML file.")

        data = read_safe_text_file(path)
        pipeline = load_pipeline_yaml(path)

        return {
            "ok": True,
            "path": data["relative_path"],
            "pipeline": {
                "pipeline_id": pipeline.pipeline_id,
                "version": pipeline.version,
                "modality": pipeline.modality,
                "description": pipeline.description,
                "nodes_total": len(pipeline.nodes),
                "nodes": [
                    {
                        "id": node.id,
                        "name": node.name,
                        "backend": node.backend,
                        "parallel_level": node.parallel_level,
                        "depends_on": node.depends_on,
                    }
                    for node in pipeline.nodes
                ],
            },
            "raw": data["content"],
        }
    except Exception as exc:
        raise_api_error(exc)


@router.get("/api/reports/dataset-evaluation")
def get_dataset_evaluation_report() -> dict[str, Any]:
    base = Path("outputs/reports") / "dataset_evaluation"

    return {
        "ok": True,
        "dataset_summary": _read_json_if_exists(base / "dataset_summary.json"),
        "subject_qc_table": _read_text_if_exists(base / "subject_qc_table.csv"),
        "exclusion_recommendations": _read_text_if_exists(base / "exclusion_recommendations.csv"),
        "report_markdown": _read_text_if_exists(base / "dataset_evaluation_report.md"),
        "report_html": _read_text_if_exists(base / "dataset_evaluation_report.html"),
    }


@router.get("/api/files/read")
def read_file(path: str = Query(...)) -> dict[str, Any]:
    try:
        return read_safe_text_file(path)
    except PathSafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise_api_error(exc)


@router.get("/api/logs/read")
def api_read_log(path: str = Query(...)) -> dict[str, Any]:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("outputs/logs/") and "/logs/" not in normalized:
        raise HTTPException(status_code=403, detail="Only logs/ files can be read here.")

    if not normalized.endswith(".log"):
        raise HTTPException(status_code=403, detail="Only .log files can be read here.")

    try:
        return read_safe_text_file(path)
    except PathSafetyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise_api_error(exc)
