from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.core.config import ConfigService
from src.backend.app.schemas.desktop import (
    AssistantChatRequest,
    AssistantChatResponse,
    BidsValidationResponse,
    BoldReferenceReadinessResponse,
    ConversionDryRunRequest,
    ConversionDryRunResponse,
    DataReadinessResponse,
    DatasetDiagnosticsPackageResponse,
    DatasetDiagnosticsPackageStatusResponse,
    DatasetDiagnosticsPackageVerifyResponse,
    DatasetImportHistoryResponse,
    DatasetImportRecord,
    DatasetImportRequest,
    DatasetImportResponse,
    DatasetSummary,
    DicomPreflightResponse,
    HealthResponse,
    ImagePlane,
    ImagePreviewResponse,
    ImageSourcesResponse,
    ImageValidationReport,
    ModelStatus,
    MotionMetricsDraftResponse,
    MotionQcReadinessResponse,
    NiftiQcSnapshotResponse,
    NiftiThumbnailResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    ProjectDetail,
    ProjectSummary,
    QcDashboardFingerprintResponse,
    QcDashboardReportResponse,
    RsfmriQcPlanningReportResponse,
    SpmRealignDryRunResponse,
    SpmRealignWrapperSkeletonResponse,
    StudyOverview,
    TaskApprovalRequest,
    TaskApprovalResponse,
    TaskArtifactsResponse,
    TaskAuditPackageResponse,
    TaskDetail,
    TaskDiagnosticsResponse,
    TaskEvent,
    TaskLogEntry,
)
from src.backend.app.services.bids_validation import validate_bids
from src.backend.app.services.bold_reference_readiness import build_bold_reference_readiness
from src.backend.app.services.conversion_planner import plan_conversion
from src.backend.app.services.data_readiness import build_data_readiness
from src.backend.app.services.dicom_preflight import build_dicom_preflight
from src.backend.app.services.image_preview import (
    build_image_preview,
    build_image_validation_report,
    list_image_sources,
)
# Legacy helper functions below the mounted route section remain directly
# callable in characterization tests until their service extraction completes.
from src.backend.app.services.mock_store import mock_store
from src.backend.app.services.motion_metrics_draft import build_motion_metrics_draft
from src.backend.app.services.motion_qc_readiness import build_motion_qc_readiness
from src.backend.app.services.nifti_qc_snapshot import build_nifti_qc_snapshot
from src.backend.app.services.nifti_thumbnail import build_nifti_thumbnail
from src.backend.app.services.pipeline_runner import run_pipeline_task
from src.backend.app.services.qc_dashboard_fingerprint import collect_qc_dashboard_fingerprint_roots
from src.backend.app.services.qc_dashboard_report import (
    build_qc_dashboard_report,
    load_latest_qc_dashboard_report,
)
from src.backend.app.services.rawdata_fingerprint import build_rawdata_fingerprint
from src.backend.app.services.rsfmri_qc_planning_report import build_rsfmri_qc_planning_report
from src.backend.app.services.spm_realign_dry_run import build_spm_realign_dry_run
from src.backend.app.services.spm_realign_wrapper_skeleton import build_spm_realign_wrapper_skeleton
from src.backend.app.services.task_manager import task_manager

router = APIRouter()

DESKTOP_HEALTH_NONCE_HEADER = "X-MedImage-Desktop-Health-Nonce"
DESKTOP_HEALTH_PROOF_HEADER = "X-MedImage-Desktop-Health-Proof"


def get_dashboard_store(
    store: ProjectStore = Depends(get_project_store),
) -> ProjectStore:
    """Expose the application-owned store through an overridable dependency."""

    return store


def _safe_artifact_part(value: str) -> str:
    return (
        "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)[:80]
        or "project"
    )


def _zip_if_exists(archive: zipfile.ZipFile, source_path: str | None, arcname: str) -> None:
    if not source_path:
        return
    path = Path(source_path)
    if path.is_file():
        archive.write(path, arcname=arcname)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_checksum_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        if digest and name:
            checksums[name] = digest
    return checksums


def _diagnostics_package_safety_flags() -> dict[str, bool]:
    return {
        "read_only_validation": True,
        "rawdata_not_bundled": True,
        "diagnostics_only": True,
        "no_matlab_execution": True,
    }


def _build_import_file_inventory(imports: list[DatasetImportRecord]) -> dict[str, Any]:
    roots: list[dict[str, Any]] = []
    total_files = 0
    extension_counts: dict[str, int] = {}
    for item in imports:
        root = Path(item.path)
        root_extensions: dict[str, int] = {}
        root_file_count = 0
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                root_file_count += 1
                ext = path.suffix.lower() or "<none>"
                root_extensions[ext] = root_extensions.get(ext, 0) + 1
                extension_counts[ext] = extension_counts.get(ext, 0) + 1
        total_files += root_file_count
        roots.append(
            {
                "dataset_id": item.dataset_id,
                "path": item.path,
                "dataset_type": item.dataset_type,
                "exists": root.exists(),
                "file_count": root_file_count,
                "extension_counts": dict(sorted(root_extensions.items())),
            }
        )
    return {
        "total_files": total_files,
        "extension_counts": dict(sorted(extension_counts.items())),
        "roots": roots,
    }


@router.get("/api/health", response_model=HealthResponse)
def api_health(
    response: Response,
    desktop_health_nonce: Annotated[
        str | None, Header(alias=DESKTOP_HEALTH_NONCE_HEADER)
    ] = None,
) -> HealthResponse:
    settings = ConfigService().server
    if settings.desktop_session_token and desktop_health_nonce:
        proof = hmac.new(
            settings.desktop_session_token.encode("utf-8"),
            desktop_health_nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response.headers[DESKTOP_HEALTH_PROOF_HEADER] = proof
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=settings.api_version,
    )


@router.get("/api/projects", response_model=list[ProjectSummary])
def list_projects(store: ProjectStore = Depends(get_dashboard_store)) -> list[ProjectSummary]:
    return store.list_projects()


@router.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: str,
    store: ProjectStore = Depends(get_dashboard_store),
) -> ProjectDetail:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return project


@router.get("/api/studies/{study_id}/overview", response_model=StudyOverview)
def get_study_overview(
    study_id: str,
    store: ProjectStore = Depends(get_dashboard_store),
) -> StudyOverview:
    overview = store.get_study_overview(study_id)
    if not overview:
        raise HTTPException(status_code=404, detail=f"Study not found: {study_id}")
    return overview


@router.get("/api/datasets/summary", response_model=DatasetSummary)
def get_dataset_summary(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetSummary:
    summary = store.get_dataset_summary(project_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return summary


@router.get("/api/datasets/imports", response_model=DatasetImportHistoryResponse)
def get_dataset_imports(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetImportHistoryResponse:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    records = [DatasetImportRecord(**item) for item in store.list_import_records(project_id)]
    return DatasetImportHistoryResponse(ok=True, project_id=project_id, imports=records)


@router.get("/api/datasets/dicom/preflight", response_model=DicomPreflightResponse)
def get_dicom_preflight(
    project_id: str = Query(...),
    path: str | None = Query(default=None),
    max_files: int = Query(default=2000, ge=1, le=10000),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DicomPreflightResponse:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    roots = [path] if path else list(store.list_import_paths(project_id))
    demo_data = Path("data/DemoData")
    if not path and demo_data.exists() and str(demo_data) not in roots:
        roots.append(str(demo_data))
    return build_dicom_preflight(project_id=project_id, roots=roots, max_files=max_files)


@router.post("/api/datasets/diagnostics/package", response_model=DatasetDiagnosticsPackageResponse)
def create_dataset_diagnostics_package(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetDiagnosticsPackageResponse:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    imports = [DatasetImportRecord(**item) for item in store.list_import_records(project_id)]
    file_inventory = _build_import_file_inventory(imports)
    search_roots = store.list_import_paths(project_id)
    sources = list_image_sources(project_id=project_id, search_roots=search_roots)
    validation = build_image_validation_report(
        project_id=project_id,
        expected_sequences=project.sequences,
        search_roots=search_roots,
    )
    dicom_roots = [item.path for item in imports if item.dataset_type == "dicom"]
    dicom_preflight = (
        build_dicom_preflight(project_id=project_id, roots=dicom_roots) if dicom_roots else None
    )
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    package_dir = Path("outputs/reports/import_diagnostics") / _safe_artifact_part(project_id)
    package_dir.mkdir(parents=True, exist_ok=True)
    json_path = package_dir / "import_diagnostics_package.json"
    report_path = package_dir / "import_diagnostics_package.md"
    zip_path = package_dir / "import_diagnostics_package.zip"
    checksum_path = package_dir / "CHECKSUMS.sha256"
    payload = {
        "ok": validation.ok,
        "project_id": project_id,
        "generated_at": generated_at,
        "package_dir": str(package_dir),
        "report_path": str(report_path),
        "json_path": str(json_path),
        "zip_path": str(zip_path),
        "checksum_path": str(checksum_path),
        "safety_flags": _diagnostics_package_safety_flags(),
        "project": project.model_dump(),
        "imports": [item.model_dump() for item in imports],
        "file_inventory": file_inventory,
        "image_sources": sources.model_dump(),
        "validation": validation.model_dump(),
        "dicom_preflight": dicom_preflight.model_dump() if dicom_preflight else None,
        "artifacts": {
            "manifest_path": sources.manifest_path,
            "validation_report_path": validation.report_path,
            "validation_json_path": validation.json_path,
            "dicom_preflight_report_path": dicom_preflight.report_path if dicom_preflight else None,
            "dicom_preflight_json_path": dicom_preflight.json_path if dicom_preflight else None,
            "zip_path": str(zip_path),
            "checksum_path": str(checksum_path),
        },
    }
    report_text = _render_import_diagnostics_markdown(payload)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    package_files: list[tuple[Path, str]] = [
        (report_path, "import_diagnostics_package.md"),
        (json_path, "import_diagnostics_package.json"),
    ]
    for source_path, arcname in [
        (sources.manifest_path, "artifacts/image_source_manifest.json"),
        (validation.report_path, "artifacts/image_validation_report.md"),
        (validation.json_path, "artifacts/image_validation_report.json"),
        (
            dicom_preflight.report_path if dicom_preflight else None,
            "artifacts/dicom_preflight_report.md",
        ),
        (
            dicom_preflight.json_path if dicom_preflight else None,
            "artifacts/dicom_preflight_result.json",
        ),
    ]:
        if source_path and Path(source_path).is_file():
            package_files.append((Path(source_path), arcname))
    checksums = {arcname: _sha256_file(path) for path, arcname in package_files}
    checksum_path.write_text(
        "".join(f"{digest}  {arcname}\n" for arcname, digest in sorted(checksums.items())),
        encoding="utf-8",
    )
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in package_files:
            archive.write(path, arcname=arcname)
        archive.write(checksum_path, arcname="CHECKSUMS.sha256")
    return DatasetDiagnosticsPackageResponse(
        ok=validation.ok,
        project_id=project_id,
        generated_at=generated_at,
        package_dir=str(package_dir),
        report_path=str(report_path),
        json_path=str(json_path),
        zip_path=str(zip_path),
        checksum_path=str(checksum_path),
        report_text=report_text,
        checksums=checksums,
        safety_flags=_diagnostics_package_safety_flags(),
        file_inventory=file_inventory,
        manifest_path=sources.manifest_path,
        validation_report_path=validation.report_path,
        import_count=len(imports),
        image_source_count=len(sources.manifest),
        validation_issue_count=len(validation.issues),
        dicom_preflight_report_path=dicom_preflight.report_path if dicom_preflight else None,
        dicom_preflight_json_path=dicom_preflight.json_path if dicom_preflight else None,
        dicom_file_count=dicom_preflight.dicom_file_count if dicom_preflight else 0,
        dicom_series_count=dicom_preflight.series_count if dicom_preflight else 0,
    )


@router.get(
    "/api/datasets/diagnostics/package/latest",
    response_model=DatasetDiagnosticsPackageStatusResponse,
)
def get_latest_dataset_diagnostics_package(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetDiagnosticsPackageStatusResponse:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    package_dir = Path("outputs/reports/import_diagnostics") / _safe_artifact_part(project_id)
    json_path = package_dir / "import_diagnostics_package.json"
    report_path = package_dir / "import_diagnostics_package.md"
    zip_path = package_dir / "import_diagnostics_package.zip"
    checksum_path = package_dir / "CHECKSUMS.sha256"
    if not json_path.is_file():
        return DatasetDiagnosticsPackageStatusResponse(
            ok=False,
            project_id=project_id,
            errors=["No import diagnostics package has been generated yet."],
            next_actions=["Generate a handoff package from Advanced Mode -> Import Diagnostics."],
        )
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return DatasetDiagnosticsPackageStatusResponse(
            ok=False,
            project_id=project_id,
            errors=[f"Failed to parse import diagnostics package: {exc}"],
            next_actions=["Regenerate the import diagnostics handoff package."],
        )
    validation = (
        payload.get("validation", {}) if isinstance(payload.get("validation"), dict) else {}
    )
    image_sources = (
        payload.get("image_sources", {}) if isinstance(payload.get("image_sources"), dict) else {}
    )
    dicom_preflight = (
        payload.get("dicom_preflight", {})
        if isinstance(payload.get("dicom_preflight"), dict)
        else {}
    )
    imports = payload.get("imports", []) if isinstance(payload.get("imports"), list) else []
    file_inventory = (
        payload.get("file_inventory") if isinstance(payload.get("file_inventory"), dict) else {}
    )
    latest = DatasetDiagnosticsPackageResponse(
        ok=bool(payload.get("ok", False)),
        project_id=project_id,
        generated_at=str(payload.get("generated_at", "")),
        package_dir=str(payload.get("package_dir") or package_dir),
        report_path=str(payload.get("report_path") or report_path),
        json_path=str(payload.get("json_path") or json_path),
        zip_path=str(payload.get("zip_path") or zip_path),
        checksum_path=str(payload.get("checksum_path") or checksum_path),
        report_text=report_path.read_text(encoding="utf-8") if report_path.is_file() else "",
        checksums=_parse_checksum_file(checksum_path),
        safety_flags=payload.get("safety_flags")
        if isinstance(payload.get("safety_flags"), dict)
        else _diagnostics_package_safety_flags(),
        file_inventory=file_inventory,
        manifest_path=(payload.get("artifacts") or {}).get("manifest_path")
        if isinstance(payload.get("artifacts"), dict)
        else None,
        validation_report_path=(payload.get("artifacts") or {}).get("validation_report_path")
        if isinstance(payload.get("artifacts"), dict)
        else None,
        import_count=len(imports),
        image_source_count=len(image_sources.get("manifest", [])),
        validation_issue_count=len(validation.get("issues", [])),
        dicom_preflight_report_path=(payload.get("artifacts") or {}).get(
            "dicom_preflight_report_path"
        )
        if isinstance(payload.get("artifacts"), dict)
        else None,
        dicom_preflight_json_path=(payload.get("artifacts") or {}).get("dicom_preflight_json_path")
        if isinstance(payload.get("artifacts"), dict)
        else None,
        dicom_file_count=int(dicom_preflight.get("dicom_file_count") or 0),
        dicom_series_count=int(dicom_preflight.get("series_count") or 0),
    )
    return DatasetDiagnosticsPackageStatusResponse(ok=True, project_id=project_id, latest=latest)


@router.post(
    "/api/datasets/diagnostics/package/verify",
    response_model=DatasetDiagnosticsPackageVerifyResponse,
)
def verify_dataset_diagnostics_package(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetDiagnosticsPackageVerifyResponse:
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    checked_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    package_dir = Path("outputs/reports/import_diagnostics") / _safe_artifact_part(project_id)
    zip_path = package_dir / "import_diagnostics_package.zip"
    checksum_path = package_dir / "CHECKSUMS.sha256"
    errors: list[str] = []
    failed_files: list[str] = []
    missing_files: list[str] = []

    checksums = _parse_checksum_file(checksum_path)
    if not zip_path.is_file():
        errors.append(f"Missing handoff ZIP: {zip_path}")
    if not checksums:
        errors.append(f"Missing or empty checksum manifest: {checksum_path}")
    if errors:
        return DatasetDiagnosticsPackageVerifyResponse(
            ok=False,
            project_id=project_id,
            checked_at=checked_at,
            zip_path=str(zip_path),
            checksum_path=str(checksum_path),
            errors=errors,
        )

    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
            for arcname, expected_digest in sorted(checksums.items()):
                if arcname not in names:
                    missing_files.append(arcname)
                    continue
                actual_digest = _sha256_bytes(archive.read(arcname))
                if actual_digest != expected_digest:
                    failed_files.append(arcname)
    except Exception as exc:
        errors.append(f"Failed to verify handoff ZIP: {exc}")

    passed_files = max(0, len(checksums) - len(failed_files) - len(missing_files))
    ok = not errors and not failed_files and not missing_files
    return DatasetDiagnosticsPackageVerifyResponse(
        ok=ok,
        project_id=project_id,
        checked_at=checked_at,
        zip_path=str(zip_path),
        checksum_path=str(checksum_path),
        checked_files=len(checksums),
        passed_files=passed_files,
        failed_files=failed_files,
        missing_files=missing_files,
        errors=errors,
    )


@router.post("/api/datasets/import", response_model=DatasetImportResponse)
def import_dataset(
    request: DatasetImportRequest,
    store: ProjectStore = Depends(get_dashboard_store),
) -> DatasetImportResponse:
    if not request.path.strip():
        raise HTTPException(status_code=400, detail="Dataset path is required")
    try:
        response = store.import_dataset(request)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {request.project_id}"
        ) from exc
    sources = list_image_sources(
        project_id=request.project_id, search_roots=store.list_import_paths(request.project_id)
    )
    project = store.get_project(request.project_id)
    validation = build_image_validation_report(
        project_id=request.project_id,
        expected_sequences=project.sequences if project else [],
        search_roots=store.list_import_paths(request.project_id),
    )
    warnings = list(sources.warnings)
    if not Path(request.path).exists():
        warnings.append(f"Imported path does not exist yet: {request.path}")
    warnings.extend(issue.message for issue in validation.issues if issue.severity == "warning")
    return response.model_copy(
        update={
            "manifest_path": sources.manifest_path,
            "image_source_count": len(sources.manifest),
            "validation_report_path": validation.report_path,
            "validation_report_text": validation.report_text,
            "validation_issue_count": len(validation.issues),
            "warnings": warnings,
        }
    )


@router.get("/api/models/status", response_model=ModelStatus)
def get_model_status(
    project_id: str = Query(...),
    store: ProjectStore = Depends(get_dashboard_store),
) -> ModelStatus:
    status = store.get_model_status(project_id)
    if not status:
        raise HTTPException(
            status_code=404, detail=f"Model status not found for project: {project_id}"
        )
    return status


def list_tasks() -> list[TaskLogEntry]:
    return mock_store.list_tasks()


def get_task(task_id: str) -> TaskDetail:
    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task


def get_task_events(task_id: str) -> list[TaskEvent]:
    if not mock_store.get_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task_manager.list_events(task_id)


async def approve_task(task_id: str, request: TaskApprovalRequest) -> TaskApprovalResponse:
    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if task.execution_mode != "external_smoke":
        raise HTTPException(
            status_code=400, detail="Only external_smoke tasks can receive run-level approval"
        )
    if not request.approved:
        raise HTTPException(
            status_code=403, detail="approved=true is required before launching approved smoke"
        )
    if not request.approved_by.strip():
        raise HTTPException(status_code=400, detail="approved_by is required")

    approval = mock_store.add_approval(
        task_id,
        approved=True,
        approved_by=request.approved_by.strip(),
        approval_scope=request.approval_scope,
        safety_flags=request.safety_flags,
    )
    await task_manager.update_task(
        task_id,
        status="running",
        progress=max(task.progress, 5),
        message=f"Approved external smoke run queued by {approval.approved_by}",
        source="approval_gate",
        metadata={"approval_id": approval.approval_id, "approval_scope": approval.approval_scope},
    )
    approved_request = PipelineRunRequest(
        project_id=task.project_id,
        pipeline_id=task.pipeline_id,
        model_id=task.model_id,
        input_sequences=task.input_sequences,
        output_type=task.output_type,
        execution_mode="external_smoke",
        external_smoke_mode="approved_smoke",
        approved=True,
        approved_by=approval.approved_by,
    )
    asyncio.create_task(run_pipeline_task(task_id, approved_request, task_manager))
    return TaskApprovalResponse(ok=True, approval=approval, message="Approved smoke run queued")


def get_task_diagnostics(task_id: str) -> TaskDiagnosticsResponse:
    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _build_task_diagnostics(task)


def get_task_artifacts(task_id: str) -> TaskArtifactsResponse:
    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    payload = _load_artifact_payload(task)
    return TaskArtifactsResponse(
        ok=True,
        task_id=task_id,
        result_path=task.result_path,
        artifacts=dict(payload.get("artifacts", {})),
        approval=mock_store.get_latest_approval(task_id),
        errors=list(payload.get("errors", [])),
    )


def generate_task_audit_package(task_id: str) -> TaskAuditPackageResponse:
    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    diagnostics = _build_task_diagnostics(task)
    artifact_response = TaskArtifactsResponse(
        ok=True,
        task_id=task_id,
        result_path=task.result_path,
        artifacts=dict(_load_artifact_payload(task).get("artifacts", {})),
        approval=mock_store.get_latest_approval(task_id),
        errors=diagnostics.errors,
    )
    return _write_task_audit_package(task, diagnostics, artifact_response)


async def run_pipeline(request: PipelineRunRequest) -> PipelineRunResponse:
    if request.execution_mode != "simulated":
        reject_execution_contract("dashboard.pipeline", project_id=request.project_id)
    if not request.input_sequences:
        raise HTTPException(status_code=400, detail="input_sequences must not be empty")
    if (
        request.execution_mode == "external_smoke"
        and request.external_smoke_mode == "approved_smoke"
    ):
        if not request.approved:
            raise HTTPException(
                status_code=403, detail="approved=true is required for approved_smoke"
            )
        if not (request.approved_by or "").strip():
            raise HTTPException(
                status_code=400, detail="approved_by is required for approved_smoke"
            )
    try:
        task = task_manager.create_pipeline_task(request)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Project not found: {request.project_id}"
        ) from exc
    if (
        request.execution_mode == "external_smoke"
        and request.external_smoke_mode == "approved_smoke"
    ):
        approval = mock_store.add_approval(
            task.id,
            approved=True,
            approved_by=(request.approved_by or "").strip(),
            safety_flags={
                "rawdata_read_only": True,
                "no_dparsf_blackbox": True,
                "matlab_external_execution": True,
            },
        )
        mock_store.append_task_event(
            task.id,
            status=task.status,
            progress=task.progress,
            message=f"Run-level approval recorded by {approval.approved_by}",
            source="approval_gate",
            metadata={"approval_id": approval.approval_id},
        )
    asyncio.create_task(run_pipeline_task(task.id, request, task_manager))
    return PipelineRunResponse(task_id=task.id, status=task.status)


@router.websocket("/ws/tasks/{task_id}")
async def task_stream(
    websocket: WebSocket,
    task_id: str,
    store: ProjectStore = Depends(get_dashboard_store),
) -> None:
    await websocket.accept()
    if not store.get_task(task_id):
        await websocket.send_json(
            {
                "task_id": task_id,
                "status": "failed",
                "progress": 0,
                "message": f"Task not found: {task_id}",
                "timestamp": "",
            }
        )
        await websocket.close(code=1008)
        return

    queue = await task_manager.subscribe(task_id)
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message.model_dump())
            if message.status in {"completed", "failed"}:
                await websocket.close()
                return
    except WebSocketDisconnect:
        return
    finally:
        task_manager.unsubscribe(task_id, queue)


def assistant_chat(request: AssistantChatRequest) -> AssistantChatResponse:
    from src.backend.app.services.assistant_service import build_assistant_reply

    reply = build_assistant_reply(
        store=mock_store,
        project_id=request.project_id,
        message=request.message,
    )
    if reply is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {request.project_id}")
    return AssistantChatResponse(reply=reply)


def image_preview(
    project_id: str = Query(...),
    subject_id: str | None = Query(default=None),
    sequence: str = Query(default="T1"),
    slice_index: int | None = Query(default=None, ge=0),
    plane: ImagePlane = Query(default="axial"),
) -> ImagePreviewResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    search_roots = mock_store.list_import_paths(project_id)
    return build_image_preview(
        project_id=project_id,
        subject_id=subject_id,
        sequence=sequence,
        slice_index=slice_index,
        plane=plane,
        search_roots=search_roots,
    )


def image_sources(project_id: str = Query(...)) -> ImageSourcesResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return list_image_sources(
        project_id=project_id, search_roots=mock_store.list_import_paths(project_id)
    )


def image_manifest(project_id: str = Query(...)) -> ImageSourcesResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return list_image_sources(
        project_id=project_id, search_roots=mock_store.list_import_paths(project_id)
    )


def image_validation(project_id: str = Query(...)) -> ImageValidationReport:
    project = mock_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_image_validation_report(
        project_id=project_id,
        expected_sequences=project.sequences,
        search_roots=mock_store.list_import_paths(project_id),
    )


def post_qc_dashboard_report(
    project_id: str,
    cache: str = "off",
) -> QcDashboardReportResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if cache not in ("off", "prefer", "refresh"):
        raise HTTPException(
            status_code=400, detail=f"Invalid cache mode: {cache}. Use off, prefer, or refresh."
        )
    return build_qc_dashboard_report(project_id, cache_mode=cache)


def get_latest_qc_dashboard_report(project_id: str) -> QcDashboardReportResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    result = load_latest_qc_dashboard_report(project_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail="No QC dashboard report has been generated yet."
        )
    return result


def get_qc_dashboard_fingerprint(project_id: str) -> QcDashboardFingerprintResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    project = mock_store.get_project(project_id)
    metadata = (project.metadata if isinstance(project.metadata, dict) else {}) if project else {}
    roots = collect_qc_dashboard_fingerprint_roots(metadata)
    fp = build_rawdata_fingerprint(roots)
    return QcDashboardFingerprintResponse(
        ok=fp.ok,
        project_id=project_id,
        fingerprint=fp,
        roots=fp.roots,
        warnings=fp.warnings,
        errors=fp.errors,
        safety_flags={
            "read_only": True,
            "rawdata_not_modified": True,
            "metadata_only": True,
            "no_cache_files_created": True,
            "no_preprocessing_executed": True,
            "no_external_tools_executed": True,
        },
    )


def get_project_data_readiness(project_id: str) -> DataReadinessResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_data_readiness(project_id)


def get_project_bids_validation(project_id: str) -> BidsValidationResponse:
    project = mock_store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    roots: list[str] = []
    rawdata = metadata.get("rawdata_dir")
    if rawdata and isinstance(rawdata, str):
        roots.append(rawdata)
    try:
        import_roots = mock_store.list_import_paths(project_id)
        for r in import_roots:
            if r not in roots:
                roots.append(r)
    except Exception:
        pass
    result = validate_bids(roots)
    result.project_id = project_id
    return result


def get_project_bold_reference_readiness(project_id: str) -> BoldReferenceReadinessResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_bold_reference_readiness(project_id)


def post_rsfmri_qc_planning_report(project_id: str) -> RsfmriQcPlanningReportResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_rsfmri_qc_planning_report(project_id)


def post_motion_metrics_draft(project_id: str) -> MotionMetricsDraftResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_motion_metrics_draft(project_id)


def post_spm_realign_dry_run(project_id: str) -> SpmRealignDryRunResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_spm_realign_dry_run(project_id)


def post_spm_realign_wrapper_skeleton(project_id: str) -> SpmRealignWrapperSkeletonResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_spm_realign_wrapper_skeleton(project_id)


def get_project_nifti_qc_snapshot(project_id: str) -> NiftiQcSnapshotResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_nifti_qc_snapshot(project_id)


def get_project_nifti_thumbnail(
    project_id: str,
    image_id: str,
    view: str = "all",
    volume_index: int | None = None,
    size: int | None = None,
) -> NiftiThumbnailResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if view not in ("axial", "coronal", "sagittal", "all"):
        raise HTTPException(status_code=400, detail=f"Invalid view: {view}")
    if volume_index is not None and volume_index < 0:
        raise HTTPException(
            status_code=400, detail=f"volume_index must be >= 0, got {volume_index}"
        )
    try:
        return build_nifti_thumbnail(project_id, image_id, view, volume_index, size)
    except ValueError as exc:
        msg = str(exc)
        if "volume_index" in msg or "out of range" in msg:
            raise HTTPException(status_code=400, detail=msg) from exc
        raise


def get_project_motion_qc_readiness(project_id: str) -> MotionQcReadinessResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return build_motion_qc_readiness(project_id)


def post_conversion_dry_run(
    project_id: str,
    request: ConversionDryRunRequest = ConversionDryRunRequest(),
) -> ConversionDryRunResponse:
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return plan_conversion(project_id, request)


def post_conversion_preflight(
    project_id: str,
) -> dict[str, Any]:
    """Read-only DICOM conversion preflight — never executes conversion.

    Returns conversion readiness, dcm2niix availability, command templates,
    safety flags, and gating status.  Does NOT call dcm2niix, write NIfTI
    files, or modify rawdata.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.dicom_conversion_execution import (
        check_dcm2niix_availability,
        run_conversion_preflight,
    )

    preflight = run_conversion_preflight(project_id)

    # Check dcm2niix availability independently
    env_flags: dict[str, str] = {}
    import os

    for flag in [
        "MEDIMAGE_ENABLE_DICOM_CONVERSION",
        "MEDIMAGE_MATLAB_ENABLED",
        "MEDIMAGE_SPM_SMOKE_ENABLED",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
        "MEDIMAGE_ENABLE_REAL_PREPROCESSING",
    ]:
        env_flags[flag] = os.environ.get(flag, "")

    availability = check_dcm2niix_availability(env=env_flags)

    return {
        "ok": preflight.ok,
        "project_id": project_id,
        "status": preflight.status,
        "conversion_disabled_by_default": preflight.conversion_disabled_by_default,
        "dcm2niix_available": availability.status == "available",
        "dcm2niix_status": availability.status,
        "dcm2niix_path": availability.executable_path,
        "dcm2niix_version": availability.version,
        "env_enabled": preflight.env_enabled,
        "missing_env_flags": preflight.missing_env_flags,
        "approval_required": preflight.approval_required,
        "audit_required": preflight.audit_required,
        "output_root_preview": preflight.output_root_preview,
        "output_dir_safe": preflight.output_dir_safe,
        "mapping_count": preflight.mapping_count,
        "mappings": [
            {
                "subject_id": m.subject_id,
                "modality": m.modality,
                "suffix": m.suffix,
                "task": m.task,
                "source_path": m.source_path,
                "suggested_relative_path": m.suggested_relative_path,
                "confidence": m.confidence,
            }
            for m in preflight.mappings
        ],
        "command_templates": [
            {
                "tool": t.tool,
                "executable": t.executable,
                "input_dir": t.input_dir,
                "output_dir": t.output_dir,
                "filename_pattern": t.filename_pattern,
                "compress": t.compress,
                "bids_sidecar": t.bids_sidecar,
                "create_bids": t.create_bids,
                "command_preview": t.command_preview,
            }
            for t in preflight.command_templates
        ],
        "warnings": preflight.warnings,
        "errors": preflight.errors,
        "blocking_issues": preflight.blocking_issues,
        "safety_flags": preflight.safety_flags.model_dump(),
    }


def post_conversion_persist_plan(
    project_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Persist a conversion approval plan and reserve a run directory.

    Evaluates the approval gate, writes metadata snapshots, and reserves
    a safe run directory.  Does NOT call dcm2niix.  Does NOT create NIfTI
    files.  Does NOT modify rawdata.

    Request body must include approval record fields and optional preflight
    snapshot.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.schemas.dicom_conversion_approval import (
        DicomConversionApprovalRecord,
    )
    from src.backend.app.services.dicom_conversion_plan_persistence import (
        persist_conversion_plan,
    )

    # Build approval record from request body
    approval = DicomConversionApprovalRecord(
        approval_id=body.get("approval_id", ""),
        project_id=project_id,
        status=body.get("status", "ready_for_review"),
        approved=body.get("approved", False),
        approved_by=body.get("approved_by", ""),
        approved_at=body.get("approved_at", ""),
        mapping_ids=body.get("mapping_ids", []),
        mappings_reviewed=body.get("mappings_reviewed", False),
        output_root=body.get("output_root", ""),
        output_root_confirmed=body.get("output_root_confirmed", False),
        output_root_under_project=body.get("output_root_under_project", False),
        output_root_not_rawdata=body.get("output_root_not_rawdata", False),
        overwrite_policy=body.get("overwrite_policy", "fail_if_exists"),
        rawdata_read_only_confirmed=body.get("rawdata_read_only_confirmed", False),
        command_templates_reviewed=body.get("command_templates_reviewed", False),
        no_shell_string_confirmed=body.get("no_shell_string_confirmed", False),
        dcm2niix_availability_confirmed=body.get("dcm2niix_availability_confirmed", False),
        env_flags_confirmed=body.get("env_flags_confirmed", False),
        rollback_policy_acknowledged=body.get("rollback_policy_acknowledged", False),
        clinical_use_prohibited_acknowledged=body.get(
            "clinical_use_prohibited_acknowledged", False
        ),
        external_tool_acknowledgement=body.get("external_tool_acknowledgement", False),
        risk_acknowledgement=body.get("risk_acknowledgement", False),
        confirm_execution=body.get("confirm_execution", False),
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")
    rawdata_dir = str(metadata.get("rawdata_dir") or "")

    result = persist_conversion_plan(
        project_id=project_id,
        approval_record=approval,
        preflight_snapshot=body.get("preflight_snapshot"),
        mappings=body.get("mappings"),
        command_templates=body.get("command_templates"),
        safety_flags=body.get("safety_flags", {}),
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
        overwrite_policy=body.get("overwrite_policy", "fail_if_exists"),
        preflight_ok=body.get("preflight_ok", True),
    )

    return result.model_dump()


def get_conversion_review_package(
    project_id: str,
    conversion_run_id: str,
) -> dict[str, Any]:
    """Read a persisted conversion review package — metadata only.

    Does NOT call dcm2niix.  Does NOT read image data.  Does NOT modify
    rawdata.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.dicom_conversion_review_package import (
        read_conversion_review_package,
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")
    rawdata_dir = str(metadata.get("rawdata_dir") or "")

    result = read_conversion_review_package(
        project_id=project_id,
        conversion_run_id=conversion_run_id,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
    )
    return result.model_dump()


def post_conversion_review_package_export(
    project_id: str,
    conversion_run_id: str,
) -> dict[str, Any]:
    """Export a metadata-only audit bundle of the review package.

    Creates a ZIP file containing only whitelisted metadata files.
    Excludes .dcm, .nii, .nii.gz, .img, .hdr files.
    Does NOT call dcm2niix.  Does NOT include image data.
    Does NOT modify rawdata.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.dicom_conversion_review_package import (
        export_conversion_review_package,
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")
    rawdata_dir = str(metadata.get("rawdata_dir") or "")

    result = export_conversion_review_package(
        project_id=project_id,
        conversion_run_id=conversion_run_id,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
    )
    return result.model_dump()


def get_conversion_release_readiness(
    project_id: str,
    conversion_run_id: str,
) -> dict[str, Any]:
    """Read-only release readiness check — never executes conversion.

    Evaluates GO/NO-GO state, disk space, rollback readiness,
    approval/audit readiness, and safety invariants.  Does NOT call
    dcm2niix, write NIfTI files, or modify rawdata.

    Public user-data conversion remains disabled.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.dicom_conversion_release_readiness import (
        evaluate_conversion_release_readiness,
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")
    output_root = f"{project_dir}/converted_bids" if project_dir else ""

    report = evaluate_conversion_release_readiness(
        project_id=project_id,
        conversion_run_id=conversion_run_id,
        output_root=output_root,
    )
    return report.model_dump()


# ═══════════════════════════════════════════════════════════════════════
# DICOM conversion public execute endpoint — Phase 4L-2
# ═══════════════════════════════════════════════════════════════════════


def post_conversion_execute(
    project_id: str,
    request_raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reject_execution_contract("dashboard.conversion", project_id=project_id)
    """Flag-gated public DICOM-to-NIfTI conversion execute endpoint.

    **Only executes when ALL preconditions pass.**  Returns blocked otherwise.

    Required env flags (all must be "1"):
    - ``MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT``
    - ``MEDIMAGE_ALLOW_USER_DATA_CONVERSION``
    - ``MEDIMAGE_ENABLE_DICOM_CONVERSION``
    - ``MEDIMAGE_ENABLE_REVIEWED_EXECUTION``
    - ``MEDIMAGE_ENABLE_REAL_PREPROCESSING``

    Required gating:
    - Release approval must be approved and not expired
    - Release readiness must be ready_for_human_release_review
    - GO/NO-GO gates must be 32/32
    - Approval/audit package must exist
    - Rawdata checksum-before must exist
    - Rollback plan must exist
    - Disk space must pass 1.5x multiplier
    - Output root must be safe

    Does NOT execute SPM/DPABI/MATLAB.  Does NOT run full preprocessing.
    Does NOT use shell=True.  Does NOT modify rawdata.
    Does NOT expose a frontend execute button.
    """
    import os as _os

    from src.backend.app.schemas.dicom_conversion_public_execution import (
        DicomConversionPublicExecutionRequest,
        DicomConversionPublicExecutionResponse,
        DicomConversionPublicExecutionSafetyFlags,
        validate_public_execution_env_flags,
        validate_public_execution_request_acknowledgements,
    )

    # ── 0. Parse request ──────────────────────────────────────────
    try:
        body = request_raw or {}
        req = DicomConversionPublicExecutionRequest(**body)
    except Exception as exc:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            errors=[f"Invalid request body: {exc}"],
            blocking_issues=[f"Request validation failed: {exc}"],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(),
        ).model_dump()

    # ── 1. Env flag check ─────────────────────────────────────────
    current_env = dict(_os.environ)
    env_ok, missing_env = validate_public_execution_env_flags(current_env)

    if not env_ok:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="disabled",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[
                f"Public conversion endpoint disabled: {len(missing_env)} "
                f"env flag(s) missing: {', '.join(missing_env)}."
            ],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                env_flags_missing=True,
                conversion_disabled_by_default=True,
            ),
        ).model_dump()

    # ── 2. Operator confirmations ─────────────────────────────────
    ok_confirm, missing_confirm = validate_public_execution_request_acknowledgements(req)
    if not ok_confirm:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[f"Operator confirmations missing: {', '.join(missing_confirm)}."],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
            ),
        ).model_dump()

    # ── 3. Project lookup ─────────────────────────────────────────
    project = mock_store.get_project(project_id)
    if not project:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[f"Project not found: {project_id}"],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
            ),
        ).model_dump()

    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")
    rawdata_dir = str(metadata.get("rawdata_dir") or "")

    if not project_dir:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=["Project directory not configured."],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
            ),
        ).model_dump()

    # ── 4. Release approval validation ────────────────────────────
    from src.backend.app.services.dicom_conversion_release_approval import (
        read_release_approval,
    )

    approval = read_release_approval(
        project_id=project_id,
        conversion_run_id=req.conversion_run_id,
        project_dir=project_dir,
    )

    approval_ok = approval.approved and not approval.blocked
    approval_status = approval.status

    if not approval_ok:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=approval.blocking_issues
            or [f"Release approval is not valid: status={approval_status}."],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                release_approval_obtained=False,
            ),
        ).model_dump()

    # ── 5. Release readiness validation ───────────────────────────
    from src.backend.app.services.dicom_conversion_release_readiness import (
        evaluate_conversion_release_readiness,
    )

    output_root = f"{project_dir}/converted_bids"
    readiness = evaluate_conversion_release_readiness(
        project_id=project_id,
        conversion_run_id=req.conversion_run_id,
        output_root=output_root,
    )

    if readiness.status != "ready_for_human_release_review":
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=readiness.blocking_issues
            or [
                f"Release readiness is '{readiness.status}', "
                f"must be 'ready_for_human_release_review'."
            ],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                release_readiness_ready=False,
                gates_32_of_32=(readiness.gates_met >= readiness.gates_total),
            ),
        ).model_dump()

    # ── 6. GO/NO-GO gate validation ───────────────────────────────
    gates_ok = readiness.gates_met >= readiness.gates_total
    if not gates_ok:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[
                f"Not all safety gates met: {readiness.gates_met}/{readiness.gates_total}."
            ],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                gates_32_of_32=False,
            ),
        ).model_dump()

    # ── 7. Approval/audit package validation ──────────────────────
    from src.backend.app.services.dicom_conversion_review_package import (
        read_conversion_review_package,
    )

    pkg = read_conversion_review_package(
        project_id,
        req.conversion_run_id,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
    )

    if not pkg.ok:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[f"Review package not readable: {pkg.errors}"],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                approval_audit_package_present=False,
            ),
        ).model_dump()

    # ── 8. Rawdata checksum-before validation ─────────────────────
    checksum_before_path = next(
        (f.path for f in pkg.files if f.kind == "rawdata_checksum_before"), ""
    )
    if not checksum_before_path or not Path(checksum_before_path).exists():
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=["Rawdata checksum-before snapshot does not exist."],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                rawdata_checksum_before_exists=False,
            ),
        ).model_dump()

    # ── 9. Rollback plan validation ───────────────────────────────
    rollback_plan_path = next((f.path for f in pkg.files if f.kind == "rollback_plan_dry_run"), "")
    if not rollback_plan_path or not Path(rollback_plan_path).exists():
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=["Rollback plan does not exist."],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                rollback_plan_exists=False,
            ),
        ).model_dump()

    # ── 10. Disk-space check ──────────────────────────────────────
    if not readiness.disk_space.ok:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[
                f"Disk space insufficient: {readiness.disk_space.free_bytes} bytes free, "
                f"{readiness.disk_space.estimated_required_bytes} estimated required."
            ],
            errors=readiness.disk_space.errors,
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                disk_space_passed=False,
            ),
        ).model_dump()

    # ── 11. Output root safety ───────────────────────────────────
    from src.backend.app.schemas.dicom_conversion_execution import (
        validate_output_root_not_under_rawdata,
        validate_output_root_under_project,
    )

    out_safe = True
    out_blockers: list[str] = []
    if not validate_output_root_under_project(output_root, project_dir):
        out_safe = False
        out_blockers.append(
            f"Output root {output_root} is not under project directory {project_dir}."
        )
    if rawdata_dir and not validate_output_root_not_under_rawdata(output_root, rawdata_dir):
        out_safe = False
        out_blockers.append(
            f"Output root {output_root} must not be inside rawdata directory {rawdata_dir}."
        )

    if not out_safe:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=out_blockers,
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                output_root_safe=False,
            ),
        ).model_dump()

    # ── 12. SPM/DPABI/MATLAB guard ────────────────────────────────
    # These remain disabled — if any env flag suggests they are enabled, block
    matlab_flag = _os.environ.get("MEDIMAGE_MATLAB_EXECUTION_ENABLED", "")
    spm_flag = _os.environ.get("MEDIMAGE_SPM_EXECUTION_ENABLED", "")
    if matlab_flag == "1" or spm_flag == "1":
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            blocking_issues=[
                "SPM/DPABI/MATLAB execution is not permitted during public conversion."
            ],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                conversion_disabled_by_default=True,
                spm_dpabi_matlab_disabled=False,
            ),
        ).model_dump()

    # ── 13. Execute ───────────────────────────────────────────────
    from datetime import datetime

    from src.backend.app.services.dicom_conversion_execution import (
        run_internal_user_dicom_conversion_from_persisted_package,
    )

    started_at = datetime.now(UTC).isoformat()
    execution_id = (
        f"pubexec-{project_id}-{req.conversion_run_id}-{int(datetime.now(UTC).timestamp())}"
    )

    try:
        internal_result = run_internal_user_dicom_conversion_from_persisted_package(
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            env=current_env,
            project_dir=project_dir,
            rawdata_dir=rawdata_dir,
        )
    except Exception as exc:
        return DicomConversionPublicExecutionResponse(
            ok=False,
            status="failed",
            project_id=project_id,
            conversion_run_id=req.conversion_run_id,
            execution_id=execution_id,
            started_at=started_at,
            finished_at=datetime.now(UTC).isoformat(),
            output_root=output_root,
            errors=[f"Internal execution failed: {exc}"],
            safety_flags=DicomConversionPublicExecutionSafetyFlags(
                env_flags_missing=False,
                public_execution_allowed=True,
                release_approval_obtained=True,
                release_readiness_ready=True,
                gates_32_of_32=True,
                approval_audit_package_present=True,
                rawdata_checksum_before_exists=True,
                rollback_plan_exists=True,
                disk_space_passed=True,
                output_root_safe=True,
                rawdata_read_only=True,
                spm_dpabi_matlab_disabled=True,
                full_preprocessing_disabled=True,
                human_release_approval_required=True,
                no_shell_execution=True,
                conversion_disabled_by_default=False,
            ),
        ).model_dump()

    finished_at = datetime.now(UTC).isoformat()

    # Map internal status to public status
    internal_status = getattr(internal_result, "status", "failed")
    if internal_status == "succeeded":
        public_status: str = "succeeded"
    elif internal_status == "warning":
        public_status = "warning"
    elif internal_status == "disabled":
        public_status = "blocked"
    elif internal_status == "blocked":
        public_status = "blocked"
    else:
        public_status = "failed"

    # Determine checksum paths
    run_dir = f"{project_dir}/conversion_runs/{req.conversion_run_id}"
    cs_after = f"{run_dir}/rawdata_checksum_after.json"
    cs_comp = f"{run_dir}/rawdata_checksum_comparison.json"
    checksum_verified = Path(cs_comp).exists() and Path(cs_after).exists()

    response = DicomConversionPublicExecutionResponse(
        ok=getattr(internal_result, "ok", False),
        status=public_status,  # type: ignore[arg-type]
        project_id=project_id,
        conversion_run_id=req.conversion_run_id,
        execution_id=execution_id,
        started_at=started_at,
        finished_at=finished_at,
        output_root=output_root,
        output_manifest_path=getattr(internal_result, "manifest_path", None) or "",
        execution_provenance_path=getattr(internal_result, "provenance_path", None) or "",
        audit_execution_start_path=f"{run_dir}/audit_execution_start.json",
        audit_execution_final_path=f"{run_dir}/audit_execution_final.json",
        checksum_before_path=checksum_before_path,
        checksum_after_path=cs_after,
        checksum_comparison_path=cs_comp,
        checksum_verified=checksum_verified,
        rollback_plan_path=rollback_plan_path,
        rollback_result_path=f"{run_dir}/rollback_result.json",
        warnings=getattr(internal_result, "warnings", []) or [],
        errors=getattr(internal_result, "errors", []) or [],
        blocking_issues=getattr(internal_result, "blocking_issues", []) or [],
        safety_flags=DicomConversionPublicExecutionSafetyFlags(
            conversion_disabled_by_default=False,
            env_flags_missing=False,
            public_execution_allowed=True,
            release_approval_obtained=True,
            release_readiness_ready=True,
            gates_32_of_32=True,
            approval_audit_package_present=True,
            rawdata_checksum_before_exists=True,
            rollback_plan_exists=True,
            disk_space_passed=True,
            output_root_safe=True,
            rawdata_read_only=True,
            spm_dpabi_matlab_disabled=True,
            full_preprocessing_disabled=True,
            human_release_approval_required=True,
            no_shell_execution=True,
        ),
    )

    return response.model_dump()


# ═══════════════════════════════════════════════════════════════════════
# Preprocessing handoff — Phase 5A
# ═══════════════════════════════════════════════════════════════════════


def post_register_converted_preprocessing_input(
    project_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Register converted BIDS/NIfTI outputs as preprocessing input.

    Discovers converted outputs from a DICOM conversion run, counts
    BOLD/T1w/NIfTI/sidecar files, detects missing subject pairings,
    and records the preprocessing input directory in project metadata.

    Does NOT execute preprocessing.  Does NOT modify rawdata.
    Does NOT call external tools.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.schemas.preprocessing_handoff import (
        PreprocessingInputRegistrationRequest,
    )
    from src.backend.app.services.preprocessing_handoff import (
        register_converted_bids_as_preprocessing_input,
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")

    req = PreprocessingInputRegistrationRequest(
        conversion_run_id=body.get("conversion_run_id", ""),
        converted_bids_dir=body.get("converted_bids_dir"),
        mode=body.get("mode", "reference"),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_use_converted_outputs=body.get("confirm_use_converted_outputs", False),
    )

    result = register_converted_bids_as_preprocessing_input(
        project_id=project_id,
        request=req,
        project_dir=project_dir,
    )
    return result.model_dump()


def post_preprocessing_plan_preview(
    project_id: str,
) -> dict[str, Any]:
    """Return a DPARSFA-style preprocessing plan preview.

    Shows all DPARSFA stages, marks which require external tools,
    and confirms preprocessing execution is disabled.

    Does NOT execute preprocessing.  Does NOT call external tools.
    Does NOT modify rawdata.  Preview-only.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.schemas.preprocessing_handoff import (
        build_default_dparsfa_style_plan,
    )

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    input_registered = bool(metadata.get("preprocessing_input_dir"))

    plan = build_default_dparsfa_style_plan(
        project_id=project_id,
        input_registered=input_registered,
    )
    return plan.model_dump()


# ═══════════════════════════════════════════════════════════════════════
# Preprocessing run workspace — Phase 5B
# ═══════════════════════════════════════════════════════════════════════


def post_create_preprocessing_run(
    project_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Create a preprocessing run workspace from converted BIDS input.

    Creates a run directory, writes README, and prepares for Python-only
    preflight execution.  No SPM/DPABI/MATLAB.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.schemas.preprocessing_run import PreprocessingRunCreateRequest
    from src.backend.app.services.preprocessing_run import create_preprocessing_run

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")

    req = PreprocessingRunCreateRequest(
        plan_id=body.get("plan_id", ""),
        preprocessing_input_dir=body.get("preprocessing_input_dir", ""),
        run_name=body.get("run_name", ""),
        confirm_use_converted_input=body.get("confirm_use_converted_input", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_python_only_execution=body.get("confirm_python_only_execution", False),
        confirm_no_spm_matlab=body.get("confirm_no_spm_matlab", False),
    )
    result = create_preprocessing_run(project_id, req, project_dir=project_dir)
    return result.model_dump()


def post_execute_python_preflight(
    project_id: str,
    preprocessing_run_id: str,
) -> dict[str, Any]:
    """Execute Python-only metadata/QC preflight stages.

    Builds input inventory, QC preflight summary, and run manifest.
    Does NOT execute SPM/DPABI/MATLAB.  Does NOT run full preprocessing.
    """
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.preprocessing_run import execute_python_preflight

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")

    result = execute_python_preflight(project_id, preprocessing_run_id, project_dir=project_dir)
    return result.model_dump()


def get_preprocessing_run(
    project_id: str,
    preprocessing_run_id: str,
) -> dict[str, Any]:
    """Get preprocessing run status and artifacts."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    from src.backend.app.services.preprocessing_run import get_preprocessing_run_status

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    project_dir = str(metadata.get("project_dir") or "")

    result = get_preprocessing_run_status(project_id, preprocessing_run_id, project_dir=project_dir)
    return result.model_dump()


# ═══════════════════════════════════════════════════════════════════════
# SPM/MATLAB runtime preflight — Phase 5C
# ═══════════════════════════════════════════════════════════════════════


def get_spm_runtime_preflight(project_id: str) -> dict[str, Any]:
    """Check MATLAB/SPM availability for synthetic smoke."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.services.spm_runtime import spm_runtime_preflight

    result = spm_runtime_preflight(project_id)
    return result.model_dump()


def post_spm_synthetic_smoke(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Generate synthetic SPM Slice Timing + Realign smoke artifacts."""
    reject_execution_contract("dashboard.spm_synthetic_smoke", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.spm_runtime import SpmSyntheticSmokeRequest
    from src.backend.app.services.spm_runtime import run_synthetic_spm_smoke

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = SpmSyntheticSmokeRequest(
        confirm_synthetic_only=body.get("confirm_synthetic_only", False),
        confirm_no_user_rawdata=body.get("confirm_no_user_rawdata", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
        matlab_executable=body.get("matlab_executable", "matlab"),
        spm_path=body.get("spm_path", ""),
    )
    result = run_synthetic_spm_smoke(project_id, req, project_dir=str(meta.get("project_dir", "")))
    return result.model_dump()


def post_spm_slice_timing_realign_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """SPM Slice Timing + Realign dry-run: batch preview, no MATLAB execution."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_spm_dry_run import SliceTimingRealignDryRunRequest
    from src.backend.app.services.preprocessing_spm_dry_run import run_slice_timing_realign_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = SliceTimingRealignDryRunRequest(
        tr=body.get("tr"),
        num_slices=body.get("num_slices"),
        slice_order=body.get("slice_order", ""),
        reference_slice=body.get("reference_slice"),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_matlab_execution=body.get("confirm_no_matlab_execution", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
    )
    result = run_slice_timing_realign_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_spm_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed Slice Timing + Realign execution: copies BOLD, runs MATLAB/SPM, captures output."""
    reject_execution_contract("dashboard.slice_timing_realign", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_spm_execution import SpmSandboxExecutionRequest
    from src.backend.app.services.preprocessing_spm_execution import run_sandbox_spm_execution

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = SpmSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_no_converted_input_modification=body.get(
            "confirm_no_converted_input_modification", False
        ),
        confirm_slice_timing_realign_only=body.get("confirm_slice_timing_realign_only", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
        preview_limit=int(body["preview_limit"]) if body.get("preview_limit") is not None else None,
        matlab_executable=body.get("matlab_executable", "matlab"),
        spm_path=body.get("spm_path", ""),
        timeout_seconds=body.get("timeout_seconds", 600),
    )
    result = run_sandbox_spm_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_sandbox_spm_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register sandbox SPM Slice Timing + Realign outputs as next-stage input."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_sandbox_spm_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_sandbox_spm_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_coreg_norm_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Coregistration + Normalization dry-run batch preview."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_coreg_norm_dry_run import CoregNormDryRunRequest
    from src.backend.app.services.preprocessing_coreg_norm_dry_run import run_coreg_norm_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = CoregNormDryRunRequest(
        registered_stage_output_id=body.get("registered_stage_output_id", ""),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_matlab_execution=body.get("confirm_no_matlab_execution", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        coreg_target=body.get("coreg_target", "mean_functional"),
        normalization_voxel_size=body.get("normalization_voxel_size", "[3,3,3]"),
        write_normalized_functional=body.get("write_normalized_functional", True),
        write_normalized_t1w=body.get("write_normalized_t1w", True),
    )
    result = run_coreg_norm_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_coreg_norm_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed Coregistration + Normalization execution."""
    reject_execution_contract("dashboard.coreg_normalize", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_coreg_norm_execution import (
        CoregNormSandboxExecutionRequest,
    )
    from src.backend.app.services.preprocessing_coreg_norm_execution import (
        run_coreg_norm_sandbox_execution,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = CoregNormSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_no_converted_input_modification=body.get(
            "confirm_no_converted_input_modification", False
        ),
        confirm_no_previous_output_modification=body.get(
            "confirm_no_previous_output_modification", False
        ),
        confirm_coreg_norm_only=body.get("confirm_coreg_norm_only", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
        matlab_executable=body.get("matlab_executable", "matlab"),
        spm_path=body.get("spm_path", ""),
        timeout_seconds=body.get("timeout_seconds", 600),
    )
    result = run_coreg_norm_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_coreg_norm_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register Coreg/Norm sandbox outputs as next-stage input."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_coreg_norm_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_coreg_norm_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_smoothing_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Smoothing batch preview dry-run."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_smoothing_dry_run import SmoothingDryRunRequest
    from src.backend.app.services.preprocessing_smoothing_dry_run import run_smoothing_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = SmoothingDryRunRequest(
        registered_stage_output_id=body.get("registered_stage_output_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        fwhm=body.get("fwhm", "[6,6,6]"),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_matlab_execution=body.get("confirm_no_matlab_execution", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_previous_outputs_readonly=body.get("confirm_previous_outputs_readonly", False),
    )
    result = run_smoothing_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_smoothing_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed Smoothing execution on copied normalized functional inputs."""
    reject_execution_contract("dashboard.smoothing", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_smoothing_execution import (
        SmoothingSandboxExecutionRequest,
    )
    from src.backend.app.services.preprocessing_smoothing_execution import (
        run_smoothing_sandbox_execution,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = SmoothingSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_no_converted_input_modification=body.get(
            "confirm_no_converted_input_modification", False
        ),
        confirm_previous_stage_readonly=body.get("confirm_previous_stage_readonly", False),
        confirm_smoothing_only=body.get("confirm_smoothing_only", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
        matlab_executable=body.get("matlab_executable", "matlab"),
        spm_path=body.get("spm_path", ""),
        timeout_seconds=body.get("timeout_seconds", 600),
    )
    result = run_smoothing_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_smoothing_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register Smoothing sandbox outputs as next-stage input."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_smoothing_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_smoothing_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_nuisance_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Nuisance regression dry-run planning."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_nuisance_dry_run import NuisanceDryRunRequest
    from src.backend.app.services.preprocessing_nuisance_dry_run import run_nuisance_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = NuisanceDryRunRequest(
        registered_stage_output_id=body.get("registered_stage_output_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        include_motion_24=body.get("include_motion_24", True),
        include_wm_csf=body.get("include_wm_csf", False),
        include_global_signal=body.get("include_global_signal", False),
        include_linear_trend=body.get("include_linear_trend", True),
        include_constant=body.get("include_constant", True),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_no_external_tools=body.get("confirm_no_external_tools", False),
        confirm_previous_outputs_readonly=body.get("confirm_previous_outputs_readonly", False),
    )
    result = run_nuisance_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_nuisance_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed Nuisance Regression execution (Python-only, metadata-first)."""
    reject_execution_contract("dashboard.nuisance_regression", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_nuisance_execution import (
        NuisanceSandboxExecutionRequest,
    )
    from src.backend.app.services.preprocessing_nuisance_execution import (
        run_nuisance_sandbox_execution,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = NuisanceSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_previous_stage_readonly=body.get("confirm_previous_stage_readonly", False),
        confirm_nuisance_regression_only=body.get("confirm_nuisance_regression_only", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
    )
    result = run_nuisance_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_nuisance_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register Nuisance Regression sandbox outputs as next-stage input."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_nuisance_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_nuisance_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_filtering_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Temporal filtering dry-run planning."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_filtering_dry_run import FilteringDryRunRequest
    from src.backend.app.services.preprocessing_filtering_dry_run import run_filtering_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = FilteringDryRunRequest(
        registered_stage_output_id=body.get("registered_stage_output_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        low_cut_hz=body.get("low_cut_hz", 0.01),
        high_cut_hz=body.get("high_cut_hz", 0.08),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_no_external_tools=body.get("confirm_no_external_tools", False),
        confirm_previous_outputs_readonly=body.get("confirm_previous_outputs_readonly", False),
    )
    result = run_filtering_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_filtering_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed Temporal Filtering execution (Python-only)."""
    reject_execution_contract("dashboard.temporal_filtering", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_filtering_execution import (
        FilteringSandboxExecutionRequest,
    )
    from src.backend.app.services.preprocessing_filtering_execution import (
        run_filtering_sandbox_execution,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = FilteringSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_previous_stage_readonly=body.get("confirm_previous_stage_readonly", False),
        confirm_temporal_filtering_only=body.get("confirm_temporal_filtering_only", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
    )
    result = run_filtering_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_filtering_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register Temporal Filtering sandbox outputs as next-stage input."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_filtering_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_filtering_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_alff_reho_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """ALFF/ReHo dry-run planning."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_alff_reho_dry_run import AlffRehoDryRunRequest
    from src.backend.app.services.preprocessing_alff_reho_dry_run import run_alff_reho_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = AlffRehoDryRunRequest(
        registered_stage_output_id=body.get("registered_stage_output_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        compute_alff=body.get("compute_alff", True),
        compute_falff=body.get("compute_falff", True),
        compute_reho=body.get("compute_reho", True),
        reho_neighbors=body.get("reho_neighbors", 27),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_no_external_tools=body.get("confirm_no_external_tools", False),
        confirm_previous_outputs_readonly=body.get("confirm_previous_outputs_readonly", False),
    )
    result = run_alff_reho_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_alff_reho_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed ALFF/ReHo execution (Python-only)."""
    reject_execution_contract("dashboard.alff_reho", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_alff_reho_execution import (
        AlffRehoSandboxExecutionRequest,
    )
    from src.backend.app.services.preprocessing_alff_reho_execution import (
        run_alff_reho_sandbox_execution,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = AlffRehoSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_previous_stage_readonly=body.get("confirm_previous_stage_readonly", False),
        confirm_alff_reho_only=body.get("confirm_alff_reho_only", False),
        confirm_no_fc_execution=body.get("confirm_no_fc_execution", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
    )
    result = run_alff_reho_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_alff_reho_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register ALFF/ReHo derivative outputs."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_alff_reho_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_alff_reho_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_fc_dry_run(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """FC dry-run planning."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_fc_dry_run import FcDryRunRequest
    from src.backend.app.services.preprocessing_fc_dry_run import run_fc_dry_run

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = FcDryRunRequest(
        filtered_stage_output_id=body.get("filtered_stage_output_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        atlas_name=body.get("atlas_name", ""),
        atlas_path=body.get("atlas_path", ""),
        correlation_method=body.get("correlation_method", "pearson"),
        fisher_z=body.get("fisher_z", True),
        confirm_dry_run_only=body.get("confirm_dry_run_only", False),
        confirm_no_image_modification=body.get("confirm_no_image_modification", False),
        confirm_no_external_tools=body.get("confirm_no_external_tools", False),
        confirm_previous_outputs_readonly=body.get("confirm_previous_outputs_readonly", False),
    )
    result = run_fc_dry_run(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_fc_sandbox_execution(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Sandboxed FC execution (Python-only, atlas-optional)."""
    reject_execution_contract("dashboard.functional_connectivity", project_id=project_id)
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_fc_execution import FcSandboxExecutionRequest
    from src.backend.app.services.preprocessing_fc_execution import run_fc_sandbox_execution

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = FcSandboxExecutionRequest(
        dry_run_id=body.get("dry_run_id", ""),
        functional_input_dir=body.get("functional_input_dir", ""),
        confirm_sandbox_copy=body.get("confirm_sandbox_copy", False),
        confirm_no_rawdata_modification=body.get("confirm_no_rawdata_modification", False),
        confirm_previous_stage_readonly=body.get("confirm_previous_stage_readonly", False),
        confirm_fc_only=body.get("confirm_fc_only", False),
        confirm_no_group_statistics=body.get("confirm_no_group_statistics", False),
        confirm_no_classification=body.get("confirm_no_classification", False),
        confirm_no_full_preprocessing=body.get("confirm_no_full_preprocessing", False),
        confirm_research_use_only=body.get("confirm_research_use_only", False),
    )
    result = run_fc_sandbox_execution(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def post_register_fc_outputs(
    project_id: str, preprocessing_run_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Register FC derivative outputs."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.schemas.preprocessing_stage_outputs import StageOutputRegistrationRequest
    from src.backend.app.services.preprocessing_stage_outputs import register_fc_outputs

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    req = StageOutputRegistrationRequest(
        execution_id=body.get("execution_id", ""),
        confirm_sandbox_outputs=body.get("confirm_sandbox_outputs", False),
        confirm_rawdata_readonly=body.get("confirm_rawdata_readonly", False),
        confirm_converted_input_readonly=body.get("confirm_converted_input_readonly", False),
        confirm_no_additional_execution=body.get("confirm_no_additional_execution", False),
        confirm_use_as_next_stage_input=body.get("confirm_use_as_next_stage_input", False),
    )
    result = register_fc_outputs(
        project_id, preprocessing_run_id, req, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def get_pipeline_report(project_id: str, preprocessing_run_id: str) -> dict[str, Any]:
    """Export preprocessing pipeline report."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.services.preprocessing_pipeline_report import generate_pipeline_report

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    result = generate_pipeline_report(
        project_id, preprocessing_run_id, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def get_pipeline_validation(project_id: str, preprocessing_run_id: str) -> dict[str, Any]:
    """End-to-end preprocessing pipeline validation."""
    if not mock_store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    from src.backend.app.services.preprocessing_pipeline_validation import (
        validate_preprocessing_pipeline,
    )

    project = mock_store.get_project(project_id)
    meta = project.metadata if project and isinstance(project.metadata, dict) else {}
    result = validate_preprocessing_pipeline(
        project_id, preprocessing_run_id, project_dir=str(meta.get("project_dir", ""))
    )
    return result.model_dump()


def _render_import_diagnostics_markdown(payload: dict[str, Any]) -> str:
    validation = payload.get("validation", {})
    dicom_preflight = payload.get("dicom_preflight", {})
    image_sources = payload.get("image_sources", {})
    artifacts = payload.get("artifacts", {})
    imports = payload.get("imports", [])
    issues = validation.get("issues", []) if isinstance(validation, dict) else []
    lines = [
        f"# Import Diagnostics Package: {payload.get('project_id')}",
        "",
        f"- Generated at: {payload.get('generated_at')}",
        f"- Validation status: {validation.get('status') if isinstance(validation, dict) else 'unknown'}",
        f"- Imports: {len(imports) if isinstance(imports, list) else 0}",
        f"- Files indexed: {payload.get('file_inventory', {}).get('total_files') if isinstance(payload.get('file_inventory'), dict) else 0}",
        f"- Image sources: {len(image_sources.get('manifest', [])) if isinstance(image_sources, dict) else 0}",
        f"- Validation issues: {len(issues) if isinstance(issues, list) else 0}",
        f"- DICOM files: {dicom_preflight.get('dicom_file_count') if isinstance(dicom_preflight, dict) else 0}",
        f"- DICOM series: {dicom_preflight.get('series_count') if isinstance(dicom_preflight, dict) else 0}",
        f"- Manifest: {artifacts.get('manifest_path') if isinstance(artifacts, dict) else 'Not generated'}",
        f"- Validation report: {artifacts.get('validation_report_path') if isinstance(artifacts, dict) else 'Not generated'}",
        f"- DICOM preflight: {artifacts.get('dicom_preflight_report_path') if isinstance(artifacts, dict) and artifacts.get('dicom_preflight_report_path') else 'Not generated'}",
        f"- Checksums: {artifacts.get('checksum_path') if isinstance(artifacts, dict) else 'Not generated'}",
        "",
        "## Safety Flags",
        "",
    ]
    safety_flags = payload.get("safety_flags", {})
    if isinstance(safety_flags, dict):
        for key, value in sorted(safety_flags.items()):
            lines.append(f"- {key}: {bool(value)}")
    lines += [
        "",
        "## Imported Roots",
        "",
    ]
    if isinstance(imports, list) and imports:
        for item in imports:
            if isinstance(item, dict):
                exists = "exists" if item.get("exists") else "missing"
                lines.append(
                    f"- [{exists}] {item.get('dataset_id')} ({item.get('dataset_type')}): {item.get('path')}"
                )
    else:
        lines.append("- No imported roots recorded.")
    lines += ["", "## File Inventory", ""]
    inventory = payload.get("file_inventory", {})
    if isinstance(inventory, dict):
        extension_counts = inventory.get("extension_counts", {})
        if isinstance(extension_counts, dict) and extension_counts:
            for ext, count in sorted(extension_counts.items()):
                lines.append(f"- {ext}: {count}")
        else:
            lines.append("- No files discovered under existing imported roots.")
    lines += ["", "## Validation Issues", ""]
    if isinstance(issues, list) and issues:
        for issue in issues:
            if isinstance(issue, dict):
                scope = " / ".join(
                    str(item) for item in [issue.get("subject_id"), issue.get("sequence")] if item
                )
                scope_text = f" ({scope})" if scope else ""
                lines.append(
                    f"- [{issue.get('severity')}] {issue.get('code')}{scope_text}: {issue.get('message')}"
                )
    else:
        lines.append("- No validation issues detected.")
    lines += ["", "## DICOM Metadata Preflight", ""]
    if isinstance(dicom_preflight, dict) and dicom_preflight:
        safety_flags = dicom_preflight.get("safety_flags", {})
        lines.append(f"- Status: {'pass' if dicom_preflight.get('ok') else 'needs review'}")
        lines.append(f"- Files: {dicom_preflight.get('dicom_file_count', 0)}")
        lines.append(f"- Sampled: {dicom_preflight.get('sampled_file_count', 0)}")
        lines.append(f"- Series: {dicom_preflight.get('series_count', 0)}")
        lines.append(
            f"- Subjects: {', '.join(dicom_preflight.get('subjects', [])) if isinstance(dicom_preflight.get('subjects'), list) else 'Not detected'}"
        )
        if isinstance(safety_flags, dict):
            for key, value in sorted(safety_flags.items()):
                lines.append(f"- {key}: {bool(value)}")
    else:
        lines.append("- No DICOM import roots were included in this package.")
    return "\n".join(lines) + "\n"


@router.get("/api/dashboard/state")
def dashboard_state(
    project_id: str = Query(default="brain-tumor-study"),
    store: ProjectStore = Depends(get_dashboard_store),
) -> dict[str, Any]:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return {
        "project": project.model_dump(),
        "study_overview": store.get_study_overview(project.study_id).model_dump(),
        "dataset_summary": store.get_dataset_summary(project.id).model_dump(),
        "model_status": store.get_model_status(project.id).model_dump(),
        "tasks": [task.model_dump() for task in store.list_tasks()],
    }


def _build_task_diagnostics(task: TaskDetail) -> TaskDiagnosticsResponse:
    payload = _load_artifact_payload(task)
    events = task_manager.list_events(task.id)
    errors = list(payload.get("errors", []))
    warnings = list(payload.get("warnings", []))
    external_tool_results = list(payload.get("external_tool_results", []))
    diagnosis: list[dict[str, Any]] = []

    for error in errors:
        diagnosis.append(
            {
                "severity": "error",
                "code": _classify_external_error(str(error)),
                "message": str(error),
            }
        )
    for warning in warnings:
        diagnosis.append({"severity": "warning", "code": "warning", "message": str(warning)})
    for result in external_tool_results:
        if isinstance(result, dict) and result.get("returncode") not in {None, 0}:
            diagnosis.append(
                {
                    "severity": "error",
                    "code": "non_zero_returncode",
                    "message": f"External command returned {result.get('returncode')}",
                    "command": result.get("command"),
                }
            )
        if isinstance(result, dict) and result.get("outputs"):
            outputs = result.get("outputs")
            missing = []
            if isinstance(outputs, dict):
                missing = [key for key, value in outputs.items() if value in {None, "", False}]
            if missing:
                diagnosis.append(
                    {
                        "severity": "error",
                        "code": "missing_expected_outputs",
                        "message": f"Missing expected outputs: {', '.join(missing)}",
                    }
                )
    if task.execution_mode == "external_smoke" and not mock_store.get_latest_approval(task.id):
        diagnosis.append(
            {
                "severity": "info",
                "code": "approval_pending",
                "message": "Manual package is reviewable; approved smoke requires explicit run-level approval.",
            }
        )
    if not diagnosis and task.status == "completed":
        diagnosis.append(
            {
                "severity": "info",
                "code": "no_critical_findings",
                "message": "No critical diagnostics were recorded.",
            }
        )

    logs = [event.message for event in events] or task.logs
    return TaskDiagnosticsResponse(
        ok=not any(item.get("severity") == "error" for item in diagnosis),
        task_id=task.id,
        status=task.status,
        diagnosis=diagnosis,
        external_tool_results=external_tool_results,
        logs=logs,
        artifacts=dict(payload.get("artifacts", {})),
        approval=mock_store.get_latest_approval(task.id),
        errors=errors,
        warnings=warnings,
    )


def _load_artifact_payload(task: TaskDetail) -> dict[str, Any]:
    payload = dict(mock_store.get_task_artifacts(task.id))
    result_path = task.result_path or str(payload.get("artifacts", {}).get("result_json", ""))
    if result_path:
        parsed = _read_json_if_exists(Path(result_path))
        if parsed:
            payload = {
                **payload,
                "artifacts": parsed.get("artifacts", payload.get("artifacts", {})),
                "external_tool_results": parsed.get(
                    "external_tool_results", payload.get("external_tool_results", [])
                ),
                "checks": parsed.get("checks", payload.get("checks", [])),
                "errors": parsed.get("errors", payload.get("errors", [])),
                "warnings": parsed.get("warnings", payload.get("warnings", [])),
                "next_actions": parsed.get("next_actions", payload.get("next_actions", [])),
            }
    return payload


def _read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_external_error(message: str) -> str:
    lower = message.lower()
    if "matlab" in lower and ("not found" in lower or "missing" in lower):
        return "missing_matlab"
    if "spm" in lower and ("not found" in lower or "missing" in lower):
        return "missing_spm_path"
    if "dpabi" in lower and ("not found" in lower or "missing" in lower):
        return "missing_dpabi_path"
    if "result json" in lower or "expected output" in lower:
        return "missing_expected_outputs"
    if "returncode" in lower or "non-zero" in lower:
        return "non_zero_returncode"
    return "external_smoke_error"


def _write_task_audit_package(
    task: TaskDetail,
    diagnostics: TaskDiagnosticsResponse,
    artifact_response: TaskArtifactsResponse,
) -> TaskAuditPackageResponse:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    package_dir = Path("outputs/reports/task_audits") / _safe_path_part(task.id)
    package_dir.mkdir(parents=True, exist_ok=True)
    events = task_manager.list_events(task.id)
    payload = {
        "ok": diagnostics.ok and not artifact_response.errors,
        "task": task.model_dump(),
        "events": [event.model_dump() for event in events],
        "diagnostics": diagnostics.model_dump(),
        "artifacts": artifact_response.model_dump(),
        "generated_at": generated_at,
        "safety": {
            "rawdata_read_only": True,
            "no_dparsf_blackbox": True,
            "approval_required_for_approved_smoke": task.execution_mode == "external_smoke",
        },
    }
    report_text = _render_task_audit_markdown(
        task, diagnostics, artifact_response, generated_at, events
    )
    json_path = package_dir / "task_audit_package.json"
    report_path = package_dir / "task_audit_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    existing_artifacts = dict(mock_store.get_task_artifacts(task.id))
    existing_artifacts["audit_package"] = {
        "package_dir": str(package_dir),
        "report_path": str(report_path),
        "json_path": str(json_path),
        "generated_at": generated_at,
    }
    mock_store.save_task_artifacts(task.id, existing_artifacts)
    return TaskAuditPackageResponse(
        ok=payload["ok"],
        task_id=task.id,
        generated_at=generated_at,
        package_dir=str(package_dir),
        report_path=str(report_path),
        json_path=str(json_path),
        report_text=report_text,
        artifacts=existing_artifacts,
        errors=diagnostics.errors + artifact_response.errors,
    )


def _render_task_audit_markdown(
    task: TaskDetail,
    diagnostics: TaskDiagnosticsResponse,
    artifacts: TaskArtifactsResponse,
    generated_at: str,
    events: list[TaskEvent],
) -> str:
    approval = diagnostics.approval
    lines = [
        f"# Task Audit Package: {task.id}",
        "",
        f"- Generated at: {generated_at}",
        f"- Run name: {task.run_name}",
        f"- Pipeline: {task.pipeline_id}",
        f"- Project: {task.project_id}",
        f"- Execution mode: {task.execution_mode}",
        f"- Status: {task.status}",
        f"- Progress: {task.progress}%",
        f"- Result path: {task.result_path or 'Pending'}",
        "",
        "## Approval",
        "",
    ]
    if approval:
        lines.extend(
            [
                f"- Approval ID: {approval.approval_id}",
                f"- Approved by: {approval.approved_by}",
                f"- Approved at: {approval.approved_at}",
                f"- Scope: {approval.approval_scope}",
                f"- Safety flags: `{json.dumps(approval.safety_flags, ensure_ascii=False)}`",
            ]
        )
    else:
        lines.append("- No run-level approval recorded.")

    lines.extend(["", "## Diagnostics", ""])
    if diagnostics.diagnosis:
        for item in diagnostics.diagnosis:
            lines.append(
                f"- [{item.get('severity', 'info')}] {item.get('code', 'diagnostic')}: {item.get('message', '')}"
            )
    else:
        lines.append("- No diagnostics recorded.")

    lines.extend(["", "## External Tool Results", ""])
    if diagnostics.external_tool_results:
        for index, result in enumerate(diagnostics.external_tool_results, start=1):
            command = result.get("command", result.get("function", f"external-run-{index}"))
            lines.append(
                f"- {index}. command: `{command}`; returncode: `{result.get('returncode', 'n/a')}`"
            )
    else:
        lines.append("- No external tool results recorded.")

    lines.extend(["", "## Artifacts", ""])
    if artifacts.artifacts:
        for key, value in artifacts.artifacts.items():
            lines.append(f"- {key}: `{value}`")
    else:
        lines.append("- No artifact paths recorded.")

    lines.extend(["", "## Event Timeline", ""])
    if events:
        for event in events:
            lines.append(
                f"- {event.timestamp} | {event.status} | {event.progress}% | {event.message}"
            )
    else:
        lines.append("- No events recorded.")

    lines.extend(
        [
            "",
            "## Safety Boundaries",
            "",
            "- rawdata remains read-only.",
            "- DPARSF/DPARSFA black-box batch flows remain prohibited.",
            "- Approved external smoke requires explicit run-level approval.",
        ]
    )
    return "\n".join(lines) + "\n"


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:120]
