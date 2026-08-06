"""Reviewed ACPC service: resolve registered T1 input, execute, and register outputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.backend.app.native_preproc.orchestrator.artifact_registry import file_sha256
from src.backend.app.native_preproc.stages.acpc_alignment import run_acpc_alignment
from src.backend.app.schemas.native_preproc_api import AcpcLandmarks, AcpcQc, AcpcRequest, AcpcResult
from src.backend.app.services.preprocessing_artifact_registry import (
    REGISTRY_FILENAME,
    append_stage_output_artifacts,
    load_artifact_registry,
)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _registry_candidates(project_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for name in ("data", "work", "derivatives"):
        root = project_root / name
        if root.is_dir():
            candidates.extend(root.rglob(REGISTRY_FILENAME))
    return sorted({path.resolve() for path in candidates})


def _resolve_artifact_path(project_root: Path, registry_path: Path, artifact: dict[str, Any]) -> Path:
    raw_path = Path(str(artifact.get("path") or ""))
    kind = str(artifact.get("path_kind") or "project_relative")
    candidate = (registry_path.parent / raw_path) if kind == "run_relative" else (project_root / raw_path if not raw_path.is_absolute() else raw_path)
    candidate = candidate.resolve()
    if not _within(candidate, project_root) or not candidate.is_file():
        raise ValueError("ACPC source artifact path is missing or outside the project boundary.")
    return candidate


def resolve_registered_t1_artifact(project_dir: str, artifact_id: str) -> tuple[Path, dict[str, Any], Path]:
    project_root = Path(project_dir).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError("ACPC project_dir must be an existing project directory.")
    for registry_path in _registry_candidates(project_root):
        data = load_artifact_registry(registry_path)
        for artifact in data.get("artifacts", []):
            if not isinstance(artifact, dict) or str(artifact.get("artifact_id") or "") != artifact_id:
                continue
            artifact_type = str(artifact.get("artifact_type") or "")
            if artifact_type not in {"converted_t1w", "t1w", "coregistered_t1w"}:
                raise ValueError("ACPC source artifact must be a registered T1w artifact.")
            return _resolve_artifact_path(project_root, registry_path, artifact), artifact, registry_path
    raise ValueError("ACPC source_t1_artifact_id was not found in approved project artifact registries.")


def execute_acpc_request(request: AcpcRequest, *, run_id: str) -> AcpcResult:
    try:
        project_root = Path(request.project_dir).expanduser().resolve()
        output_root = Path(request.output_root).expanduser().resolve() if request.output_root else project_root / "derivatives"
        derivatives_root = (project_root / "derivatives").resolve()
        if not _within(output_root, derivatives_root):
            raise ValueError("ACPC output_root must be within the project's derivatives directory.")
        source_path, source_artifact, _source_registry = resolve_registered_t1_artifact(request.project_dir, request.source_t1_artifact_id)
        subject_id = str(source_artifact.get("subject_id") or "")
        session_id = str(source_artifact.get("session_id") or "")
        stage = run_acpc_alignment(
            source_path,
            output_root,
            template_id=request.template_id,
            interpolation=request.interpolation,
            run_id=run_id,
            subject_id=subject_id,
            session_id=session_id,
            source_artifact_id=request.source_t1_artifact_id,
        )
        metrics = dict(stage.qc.metrics)
        checks = {str(key): bool(value) for key, value in dict(metrics.get("checks") or {}).items()}
        qc = AcpcQc(
            converged=bool(metrics.get("converged")),
            cost=float(metrics["nmi_after"]) if isinstance(metrics.get("nmi_after"), int | float) else None,
            checks=checks,
            review_required=bool(metrics.get("review_required", True)),
            failure_code=str(metrics.get("failure_code") or ""),
        )
        if stage.status != "succeeded":
            return AcpcResult(ok=False, status="failed", qc=qc, provenance=stage.provenance.model_dump(mode="json"), errors=list(stage.errors))
        by_type = {artifact.artifact_type: artifact for artifact in stage.output_artifacts}
        landmark_artifact = by_type["acpc_landmarks"]
        landmark_payload = json.loads(Path(landmark_artifact.path).read_text(encoding="utf-8"))
        registry_path = output_root / REGISTRY_FILENAME
        append_stage_output_artifacts(
            registry_path=registry_path,
            project_id=request.project_id,
            preprocessing_run_id=run_id,
            stage_id="auto_acpc_align",
            output_paths_by_type={artifact.artifact_type: [Path(artifact.path)] for artifact in stage.output_artifacts},
            project_dir=str(project_root),
            source_execution_id=run_id,
            backend="native_python",
            provenance_path=str((output_root / "provenance" / "auto_acpc_align_provenance.json")),
            qc_path=str((output_root / "qc" / "auto_acpc_align_qc.json")),
            metadata={
                "source_t1_artifact_id": request.source_t1_artifact_id,
                "source_t1_checksum": file_sha256(source_path),
                "template_id": request.template_id,
                "capability_level": "computed",
                "review_required": False,
            },
            source_artifact_ids=[request.source_t1_artifact_id],
        )
        return AcpcResult(
            ok=True,
            status="computed",
            transform_artifact_id=by_type["transform_matrix"].artifact_id,
            aligned_t1_artifact_id=by_type["acpc_t1w"].artifact_id,
            landmarks_artifact_id=landmark_artifact.artifact_id,
            landmarks=AcpcLandmarks(
                estimated_ac_mm=list(landmark_payload["estimated_ac_mm"]),
                estimated_pc_mm=list(landmark_payload["estimated_pc_mm"]),
                msp_normal=list(landmark_payload["msp_normal"]),
                coordinate_system=str(landmark_payload["coordinate_system"]),
            ),
            qc=qc,
            provenance=stage.provenance.model_dump(mode="json"),
            registry_path=str(registry_path),
        )
    except Exception as exc:
        return AcpcResult(ok=False, status="blocked", errors=[str(exc)])


__all__ = ["execute_acpc_request", "resolve_registered_t1_artifact"]
