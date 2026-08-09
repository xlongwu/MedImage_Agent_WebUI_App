"""Service boundary for the native full preprocessing API."""
from __future__ import annotations

import hashlib
import multiprocessing
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Empty
from threading import Thread
from typing import Any

from src.backend.app.native_preproc.orchestrator.artifact_registry import build_artifact_ref
from src.backend.app.native_preproc.orchestrator.progress import (
    initial_progress,
    load_progress,
    write_progress,
)
from src.backend.app.native_preproc.orchestrator.progress import (
    now_iso as progress_now_iso,
)
from src.backend.app.native_preproc.orchestrator.resource_planner import (
    ResourceDecision,
    plan_subject_execution,
)
from src.backend.app.native_preproc.orchestrator.runner import (
    dry_run_native_full_preproc,
    execute_native_full_preproc,
    load_native_full_run_manifest,
)
from src.backend.app.native_preproc.orchestrator.stage_graph import (
    iter_native_full_stage_specs,
    native_full_stage_graph_payload,
)
from src.backend.app.native_preproc.orchestrator.subject_worker import execute_subject_worker
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.native_preproc_api import (
    NativeFullPreprocRequest,
    NativeFullPreprocResponse,
    NativeFullStageApiResult,
)
from src.backend.app.services.preprocessing_artifact_registry import (
    REGISTRY_FILENAME,
    append_stage_output_artifacts,
    load_artifact_registry,
)

_BIDS_SUBJECT_RE = re.compile(r"sub-[A-Za-z0-9]+")
_NIFTI_SUFFIXES = (".nii", ".nii.gz")
_ATLAS_LABEL_SUFFIXES = (".json", ".tsv", ".csv")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _batch_run_id(project_id: str) -> str:
    digest = hashlib.sha256(f"{project_id}:{_now_iso()}".encode()).hexdigest()[:12]
    return f"npre-batch-{digest}"


def _resolve_registry_path(value: object, *, path_kind: object, project_root: Path | None) -> str:
    path_text = str(value or "").strip()
    if not path_text:
        return ""
    path = Path(path_text)
    if path.is_absolute():
        return str(path)
    if str(path_kind or "") == "project_relative" and project_root is not None:
        return str((project_root / path).resolve())
    registry_root = project_root if project_root is not None else Path.cwd()
    return str((registry_root / path).resolve())


def _registry_artifacts(registry: dict[str, object]) -> list[dict[str, object]]:
    artifacts = registry.get("artifacts", [])
    if not isinstance(artifacts, list):
        return []
    return [item for item in artifacts if isinstance(item, dict)]


def _artifact_subject(item: dict[str, object]) -> str:
    subject = str(item.get("subject_id") or "").strip()
    if subject:
        return subject
    text = str(item.get("path") or "")
    match = _BIDS_SUBJECT_RE.search(text)
    return match.group(0) if match else ""


def _artifact_matches(item: dict[str, object], artifact_types: tuple[str, ...]) -> bool:
    return str(item.get("artifact_type") or "") in artifact_types and bool(item.get("path"))


def _artifact_path(item: dict[str, object], project_root: Path | None) -> str:
    if not item.get("path"):
        return ""
    return _resolve_registry_path(
        item["path"],
        path_kind=item.get("path_kind"),
        project_root=project_root,
    )


def _role_sort_key(path_text: str, role: str) -> tuple[int, str]:
    name = Path(path_text).name.lower()
    penalty = 0
    if role == "bold" and ("bolda" in name or "_run-2" in name):
        penalty += 10
    if role == "t1w" and ("t1wa" in name or "_run-2" in name):
        penalty += 10
    if role == "sidecar" and ("bolda" in name or "_run-2" in name):
        penalty += 10
    return penalty, name


def _subject_artifact_path(
    registry: dict[str, object],
    subject_id: str,
    artifact_types: tuple[str, ...],
    *,
    role: str,
    project_root: Path | None,
) -> str:
    candidates: list[str] = []
    for item in _registry_artifacts(registry):
        if not _artifact_matches(item, artifact_types):
            continue
        if _artifact_subject(item) != subject_id:
            continue
        path = _artifact_path(item, project_root)
        if path:
            candidates.append(path)
    if not candidates:
        return ""
    return sorted(candidates, key=lambda value: _role_sort_key(value, role))[0]


def _sidecar_for_bold(
    registry: dict[str, object],
    subject_id: str,
    bold_path: str,
    *,
    project_root: Path | None,
) -> str:
    sidecars: list[str] = []
    for item in _registry_artifacts(registry):
        if not _artifact_matches(item, ("sidecar_json",)):
            continue
        if _artifact_subject(item) != subject_id:
            continue
        path = _artifact_path(item, project_root)
        if path:
            sidecars.append(path)
    if not sidecars:
        return ""
    if bold_path:
        bold_name = Path(bold_path).name
        expected = (
            bold_name.removesuffix(".nii.gz") + ".json"
            if bold_name.endswith(".nii.gz")
            else Path(bold_name).with_suffix(".json").name
        )
        for sidecar in sidecars:
            if Path(sidecar).name == expected:
                return sidecar
    return sorted(sidecars, key=lambda value: _role_sort_key(value, "sidecar"))[0]


def _latest_artifact_path(
    registry: dict[str, object],
    *artifact_types: str,
    project_root: Path | None = None,
) -> str:
    for artifact_type in artifact_types:
        matches = [
            item
            for item in _registry_artifacts(registry)
            if item.get("artifact_type") == artifact_type and item.get("path")
        ]
        if matches:
            latest = matches[-1]
            return _resolve_registry_path(
                latest["path"],
                path_kind=latest.get("path_kind"),
                project_root=project_root,
            )
    return ""


def _resource_candidates(
    project_root: Path | None,
    *relative_parts: str,
    suffixes: tuple[str, ...],
    stem: str = "",
) -> list[Path]:
    if project_root is None:
        return []
    resource_dir = (project_root / "resources" / Path(*relative_parts)).resolve()
    if not resource_dir.is_dir():
        return []
    candidates: list[Path] = []
    for item in resource_dir.iterdir():
        resolved = item.resolve()
        if not resolved.is_file() or not resolved.is_relative_to(resource_dir):
            continue
        name = resolved.name.lower()
        if not any(name.endswith(suffix) for suffix in suffixes):
            continue
        if stem and _resource_stem(resolved) != stem.lower():
            continue
        candidates.append(resolved)
    return sorted(candidates, key=lambda item: item.name.lower())


def _resource_stem(path: Path) -> str:
    name = path.name.lower()
    return name.removesuffix(".nii.gz") if name.endswith(".nii.gz") else path.stem.lower()


def _unique_project_resource(
    project_root: Path | None,
    *,
    resource_name: str,
    relative_parts: tuple[str, ...],
    suffixes: tuple[str, ...],
    stem: str = "",
) -> tuple[str, list[str]]:
    candidates = _resource_candidates(
        project_root,
        *relative_parts,
        suffixes=suffixes,
        stem=stem,
    )
    if len(candidates) == 1:
        return str(candidates[0]), [
            f"Resolved native preprocessing {resource_name} from project resources."
        ]
    if len(candidates) > 1:
        names = ", ".join(item.name for item in candidates)
        return "", [
            f"Multiple {resource_name} candidates found in project resources ({names}); "
            "set an explicit plan parameter."
        ]
    return "", []


def _shared_preproc_resources(
    request: NativeFullPreprocRequest,
    *,
    registry: dict[str, object] | None,
    project_root: Path | None,
) -> tuple[dict[str, str], list[str]]:
    warnings: list[str] = []
    registry_payload = registry or {}

    template = request.template or _latest_artifact_path(
        registry_payload,
        "template",
        project_root=project_root,
    )
    if not template:
        template, messages = _unique_project_resource(
            project_root,
            resource_name="template",
            relative_parts=("templates",),
            suffixes=_NIFTI_SUFFIXES,
        )
        warnings.extend(messages)

    atlas = request.atlas or _latest_artifact_path(
        registry_payload,
        "atlas",
        project_root=project_root,
    )
    if not atlas:
        atlas, messages = _unique_project_resource(
            project_root,
            resource_name="atlas",
            relative_parts=("atlases",),
            suffixes=_NIFTI_SUFFIXES,
        )
        warnings.extend(messages)

    atlas_labels = request.atlas_labels or _latest_artifact_path(
        registry_payload,
        "atlas_labels",
        project_root=project_root,
    )
    if not atlas_labels and atlas:
        atlas_labels, messages = _unique_project_resource(
            project_root,
            resource_name="atlas label file",
            relative_parts=("atlases",),
            suffixes=_ATLAS_LABEL_SUFFIXES,
            stem=_resource_stem(Path(atlas)),
        )
        warnings.extend(messages)

    return {
        "template": template,
        "atlas": atlas,
        "atlas_labels": atlas_labels,
    }, warnings


def _conversion_registry_context(
    request: NativeFullPreprocRequest,
    *,
    project_metadata: dict[str, object] | None = None,
) -> tuple[dict[str, object] | None, Path | None, str, list[str]]:
    metadata = project_metadata or {}
    conversion_run_id = request.conversion_run_id or str(metadata.get("preprocessing_conversion_run_id") or "")
    registry_path = str(metadata.get("preprocessing_input_registry_path") or "")
    project_root_text = str(metadata.get("project_dir") or "")
    project_root = Path(project_root_text).resolve() if project_root_text else None
    if not registry_path or not Path(registry_path).exists():
        return None, project_root, conversion_run_id, []

    registry = load_artifact_registry(registry_path)
    registry_conversion = str(registry.get("conversion_run_id") or metadata.get("preprocessing_conversion_run_id") or "")
    if conversion_run_id and registry_conversion and conversion_run_id != registry_conversion:
        return None, project_root, conversion_run_id, [
            f"Requested conversion_run_id={conversion_run_id} did not match registered preprocessing input conversion_run_id={registry_conversion}."
        ]
    return registry, project_root, conversion_run_id or registry_conversion, []


def _request_with_registered_conversion_inputs(
    request: NativeFullPreprocRequest,
    *,
    project_metadata: dict[str, object] | None = None,
) -> tuple[NativeFullPreprocRequest, list[str]]:
    registry, project_root, conversion_run_id, context_warnings = _conversion_registry_context(
        request,
        project_metadata=project_metadata,
    )
    if registry is None:
        shared_resources, resource_warnings = _shared_preproc_resources(
            request,
            registry=None,
            project_root=project_root,
        )
        return request.model_copy(update=shared_resources), [
            *context_warnings,
            *resource_warnings,
        ]

    updates: dict[str, object] = {"conversion_run_id": conversion_run_id}
    shared_resources, resource_warnings = _shared_preproc_resources(
        request,
        registry=registry,
        project_root=project_root,
    )
    if not request.input_bold:
        updates["input_bold"] = (
            _subject_artifact_path(
                registry,
                request.subject_id,
                ("converted_bold", "bold_4d"),
                role="bold",
                project_root=project_root,
            )
            if request.subject_id
            else _latest_artifact_path(
                registry,
                "converted_bold",
                "bold_4d",
                project_root=project_root,
            )
        )
    if not request.sidecar_json:
        updates["sidecar_json"] = (
            _sidecar_for_bold(
                registry,
                request.subject_id,
                str(updates.get("input_bold") or request.input_bold),
                project_root=project_root,
            )
            if request.subject_id
            else _latest_artifact_path(
                registry,
                "sidecar_json",
                project_root=project_root,
            )
        )
    if not request.t1w:
        updates["t1w"] = (
            _subject_artifact_path(
                registry,
                request.subject_id,
                ("converted_t1w", "t1w"),
                role="t1w",
                project_root=project_root,
            )
            if request.subject_id
            else _latest_artifact_path(
                registry,
                "converted_t1w",
                "t1w",
                project_root=project_root,
            )
        )
    updates.update(shared_resources)

    resolved = request.model_copy(update=updates)
    warnings = [*context_warnings, *resource_warnings]
    if resolved.input_bold != request.input_bold:
        warnings.append("Resolved native preprocessing BOLD input from conversion artifact registry.")
    if resolved.sidecar_json != request.sidecar_json:
        warnings.append("Resolved native preprocessing sidecar input from conversion artifact registry.")
    if resolved.t1w != request.t1w:
        warnings.append("Resolved native preprocessing T1w input from conversion artifact registry.")
    return resolved, warnings


def _registered_subject_requests(
    request: NativeFullPreprocRequest,
    *,
    project_metadata: dict[str, object] | None = None,
) -> tuple[list[NativeFullPreprocRequest], list[str]]:
    if request.input_bold:
        return [], []
    if request.input_bids_dir:
        return _bids_directory_subject_requests(request)
    registry, project_root, conversion_run_id, context_warnings = _conversion_registry_context(
        request,
        project_metadata=project_metadata,
    )
    if registry is None:
        return [], context_warnings

    shared_resources, resource_warnings = _shared_preproc_resources(
        request,
        registry=registry,
        project_root=project_root,
    )

    subject_ids = sorted(
        {
            _artifact_subject(item)
            for item in _registry_artifacts(registry)
            if _artifact_matches(item, ("converted_bold", "bold_4d"))
        }
    )
    if request.subject_id:
        subject_ids = [
            subject_id
            for subject_id in subject_ids
            if subject_id == request.subject_id
        ]
    subject_requests: list[NativeFullPreprocRequest] = []
    for subject_id in subject_ids:
        bold = _subject_artifact_path(
            registry,
            subject_id,
            ("converted_bold", "bold_4d"),
            role="bold",
            project_root=project_root,
        )
        if not bold:
            continue
        sidecar = request.sidecar_json or _sidecar_for_bold(
            registry,
            subject_id,
            bold,
            project_root=project_root,
        )
        t1w = request.t1w or _subject_artifact_path(
            registry,
            subject_id,
            ("converted_t1w", "t1w"),
            role="t1w",
            project_root=project_root,
        )
        subject_requests.append(
            request.model_copy(
                update={
                    "subject_id": subject_id,
                    "input_bold": bold,
                    "sidecar_json": sidecar,
                    "t1w": t1w,
                    "template": shared_resources["template"],
                    "atlas": shared_resources["atlas"],
                    "atlas_labels": shared_resources["atlas_labels"],
                    "conversion_run_id": conversion_run_id,
                }
            )
        )
    warnings = [*context_warnings, *resource_warnings]
    if subject_requests:
        warnings.append(
            "Resolved native preprocessing BOLD input from conversion artifact registry."
        )
        if not request.sidecar_json and any(item.sidecar_json for item in subject_requests):
            warnings.append(
                "Resolved native preprocessing sidecar input from conversion artifact registry."
            )
        if not request.t1w and any(item.t1w for item in subject_requests):
            warnings.append(
                "Resolved native preprocessing T1w input from conversion artifact registry."
            )
    if len(subject_requests) > 1:
        warnings.append(
            f"Resolved {len(subject_requests)} native preprocessing subject input set(s) from conversion artifact registry."
        )
    return subject_requests, warnings


def _bids_directory_subject_requests(
    request: NativeFullPreprocRequest,
) -> tuple[list[NativeFullPreprocRequest], list[str]]:
    """Resolve a registered BIDS tree without writing to the source dataset."""
    bids_root = Path(request.input_bids_dir).expanduser().resolve()
    if not bids_root.is_dir():
        return [], [f"Registered BIDS input directory was not found: {bids_root}"]

    bold_files = sorted(
        path.resolve()
        for path in bids_root.rglob("*")
        if path.is_file() and path.name.lower().endswith((".nii", ".nii.gz"))
        and "_bold" in path.name.lower()
    )
    subject_requests: list[NativeFullPreprocRequest] = []
    for bold in bold_files:
        match = _BIDS_SUBJECT_RE.search(str(bold.relative_to(bids_root)))
        if match is None:
            continue
        subject_id = match.group(0)
        if request.subject_id and subject_id != request.subject_id:
            continue
        sidecar_name = (
            bold.name.removesuffix(".nii.gz") + ".json"
            if bold.name.lower().endswith(".nii.gz")
            else bold.with_suffix(".json").name
        )
        sidecar = bold.with_name(sidecar_name)
        subject_root = bids_root / subject_id
        t1w_candidates = sorted(
            path.resolve()
            for path in subject_root.rglob("*")
            if path.is_file()
            and path.name.lower().endswith((".nii", ".nii.gz"))
            and "_t1w" in path.name.lower()
        )
        subject_requests.append(
            request.model_copy(
                update={
                    "subject_id": subject_id,
                    "input_bold": str(bold),
                    "sidecar_json": str(sidecar.resolve()) if sidecar.is_file() else "",
                    "t1w": str(t1w_candidates[0]) if t1w_candidates else "",
                }
            )
        )
    warnings = [
        f"Resolved {len(subject_requests)} subject input set(s) from the registered BIDS directory.",
        "The registered BIDS source is read-only; all numerical outputs use project-owned output roots.",
    ]
    return subject_requests, warnings


def _project_root(project_dir: str, project_id: str) -> Path:
    return Path(project_dir).expanduser().resolve() if project_dir else Path("outputs") / "native_preproc" / project_id


def _batch_run_dir(
    request: NativeFullPreprocRequest,
    *,
    project_id: str,
    project_dir: str,
) -> tuple[str, Path]:
    run_id = request.run_id or _batch_run_id(project_id)
    root = _project_root(project_dir, project_id)
    run_dir = (
        Path(request.output_dir).expanduser().resolve()
        if request.output_dir
        else root / "preprocessing_native_runs" / run_id
    )
    return run_id, run_dir


def _stage_status_lists(
    stage_results: list[NativeFullStageApiResult],
) -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
    completed: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    metadata_only: list[str] = []
    warning: list[str] = []
    for item in stage_results:
        subject_id = str(item.result.get("subject_id") or "")
        key = f"{subject_id}:{item.stage_id}" if subject_id else item.stage_id
        if item.status in {"succeeded", "simplified", "warning"}:
            completed.append(key)
            if item.status in {"simplified", "warning"}:
                warning.append(key)
        elif item.status == "blocked":
            blocked.append(key)
        elif item.status == "failed":
            failed.append(key)
        elif item.status == "skipped":
            skipped.append(key)
        elif item.status == "metadata_only":
            metadata_only.append(key)
    return completed, blocked, failed, skipped, metadata_only, warning


def _child_summary(response: NativeFullPreprocResponse, subject_id: str) -> dict[str, Any]:
    return {
        "subject_id": subject_id,
        "run_id": response.run_id,
        "run_dir": response.run_dir,
        "status": response.status,
        "ok": response.ok,
        "artifact_count": response.artifact_count,
        "completed_stages": response.completed_stages,
        "blocked_stages": response.blocked_stages,
        "failed_stages": response.failed_stages,
        "warning_stages": response.warning_stages,
        "manifest_path": response.manifest_path,
        "validation_report_path": response.validation_report_path,
        "final_report_path": response.final_report_path,
        "warnings": response.warnings,
        "errors": response.errors,
        "blocking_issues": response.blocking_issues,
    }


def _stage_with_subject(stage: NativeFullStageApiResult, subject_id: str) -> NativeFullStageApiResult:
    result = dict(stage.result or {})
    result.setdefault("subject_id", subject_id)
    return stage.model_copy(update={"result": result})


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _native_stage_sidecars(
    *,
    run_dir: Path,
    stage: NativeFullStageApiResult,
) -> tuple[str, str]:
    """Return existing native stage provenance/QC sidecars when available."""
    subject_id = str(stage.result.get("subject_id") or "").strip()
    stage_root = run_dir / subject_id if subject_id else run_dir
    provenance = stage_root / "provenance" / f"{stage.stage_id}_provenance.json"
    qc = stage_root / "qc" / f"{stage.stage_id}_qc.json"
    return (
        str(provenance) if provenance.is_file() else "",
        str(qc) if qc.is_file() else "",
    )


def _register_native_output_artifacts(
    *,
    project_id: str,
    run_id: str,
    run_dir: Path,
    project_dir: str,
    stage_results: list[NativeFullStageApiResult],
) -> tuple[Path, int]:
    """Persist actual native outputs into the Agent-visible run registry.

    Native preprocessing has its own execution directory, while Agent Task
    observations read run evidence from the project-owned pipeline work root.
    Only existing outputs under the project are registered; input/rawdata paths
    are neither copied into nor accepted by this output registry.
    """
    project_root = _project_root(project_dir, project_id).resolve()
    registry_path = (
        project_root / "work" / "pipeline_runs" / run_id / REGISTRY_FILENAME
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    appended_count = 0

    for stage in stage_results:
        output_paths_by_type: dict[str, list[Path]] = {}
        for artifact in stage.output_artifacts:
            artifact_type = str(artifact.get("artifact_type") or "").strip()
            artifact_path = str(artifact.get("path") or "").strip()
            if not artifact_type or not artifact_path:
                raise ValueError(f"NATIVE_OUTPUT_ARTIFACT_INVALID:{stage.stage_id}")
            candidate = Path(artifact_path).expanduser()
            if not candidate.is_absolute():
                candidate = project_root / candidate
            resolved = candidate.resolve()
            if not _is_relative_to(resolved, project_root):
                raise ValueError(f"NATIVE_OUTPUT_ARTIFACT_OUTSIDE_PROJECT:{stage.stage_id}")
            if any(part.casefold() == "rawdata" for part in resolved.parts):
                raise ValueError(f"NATIVE_OUTPUT_ARTIFACT_IN_RAWDATA:{stage.stage_id}")
            if not resolved.is_file():
                raise ValueError(f"NATIVE_OUTPUT_ARTIFACT_MISSING:{stage.stage_id}")
            output_paths_by_type.setdefault(artifact_type, []).append(resolved)

        if not output_paths_by_type:
            continue
        provenance_path, qc_path = _native_stage_sidecars(run_dir=run_dir, stage=stage)
        result = append_stage_output_artifacts(
            registry_path=registry_path,
            project_id=project_id,
            preprocessing_run_id=run_id,
            stage_id=stage.stage_id,
            output_paths_by_type=output_paths_by_type,
            project_dir=str(project_root),
            source_execution_id=f"native_full:{run_id}",
            backend=stage.backend or "native_python",
            provenance_path=provenance_path,
            qc_path=qc_path,
            metadata={
                "native_preprocessing": True,
                "stage_status": stage.status,
                "validation_status": stage.validation_status,
                "capability_level": stage.capability_level,
            },
        )
        appended_count += int(result.get("appended_artifact_count") or 0)
    return registry_path, appended_count


def _persist_response_with_native_output_registry(
    response: NativeFullPreprocResponse,
    *,
    project_id: str,
    project_dir: str,
) -> NativeFullPreprocResponse:
    """Register single-subject native outputs and preserve failure truthfulness."""
    if response.dry_run or not response.run_id or not response.run_dir:
        return response
    try:
        registry_path, artifact_count = _register_native_output_artifacts(
            project_id=project_id,
            run_id=response.run_id,
            run_dir=Path(response.run_dir).resolve(),
            project_dir=project_dir,
            stage_results=response.stage_results,
        )
    except Exception as exc:
        error = f"NATIVE_OUTPUT_REGISTRY_FAILED:{type(exc).__name__}"
        updated = response.model_copy(
            update={
                "ok": False,
                "status": "partial" if response.status == "succeeded" else response.status,
                "errors": [*response.errors, error],
                "blocking_issues": [*response.blocking_issues, "NATIVE_OUTPUT_REGISTRY_FAILED"],
            }
        )
    else:
        updated = response.model_copy(
            update={
                "warnings": [
                    *response.warnings,
                    f"Registered {artifact_count} native output artifact(s) in {registry_path.name}.",
                ]
            }
        )
    if updated.manifest_path:
        atomic_write_json(
            Path(updated.manifest_path),
            updated.model_dump(mode="json"),
            schema_version=1,
        )
    return updated


def _report_stage(
    stage_id: str,
    *,
    path: Path,
    artifact_type: str,
    warnings: list[str] | None = None,
    result: dict[str, Any] | None = None,
) -> NativeFullStageApiResult:
    specs = {spec.stage_id: spec for spec in iter_native_full_stage_specs()}
    spec = specs[stage_id]
    artifact = build_artifact_ref(path, artifact_type=artifact_type)  # type: ignore[arg-type]
    return NativeFullStageApiResult(
        stage_id=stage_id,
        display_name=spec.display_name,
        node_id=spec.node_id,
        status="metadata_only",
        capability_level="metadata_only",
        validation_status="not_applicable",
        backend="native_python",
        output_artifacts=[artifact.model_dump(mode="json")],
        warnings=warnings or [],
        result=result or {},
    )


def _batch_status(child_responses: list[NativeFullPreprocResponse]) -> str:
    if any(item.status == "failed" for item in child_responses):
        return "failed"
    if child_responses and all(item.status == "planned" for item in child_responses):
        return "planned"
    if any(item.status in {"blocked", "partial"} for item in child_responses):
        return "partial"
    if child_responses and all(item.status == "succeeded" for item in child_responses):
        return "succeeded"
    return "blocked"


def _write_batch_response(
    *,
    project_id: str,
    run_id: str,
    run_dir: Path,
    project_dir: str,
    dry_run: bool,
    child_runs: list[tuple[str, NativeFullPreprocResponse]],
    warnings: list[str],
    resource_decision: ResourceDecision | None = None,
    group_summary_enabled: bool = True,
    persist: bool = True,
) -> NativeFullPreprocResponse:
    child_summaries = [_child_summary(response, subject_id) for subject_id, response in child_runs]
    child_responses = [response for _, response in child_runs]
    stage_results: list[NativeFullStageApiResult] = []
    for subject_id, response in child_runs:
        stage_results.extend(_stage_with_subject(stage, subject_id) for stage in response.stage_results)

    group_path = run_dir / "artifacts" / "group_summary" / "native_group_summary.json"
    validation_path = run_dir / "artifacts" / "validation_report" / "native_preproc_validation_report.json"
    final_path = run_dir / "artifacts" / "final_report" / "native_preproc_final_report.json"
    manifest_path = run_dir / "native_full_run_manifest.json"
    if persist:
        run_dir.mkdir(parents=True, exist_ok=True)
        if group_summary_enabled:
            group_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.parent.mkdir(parents=True, exist_ok=True)

    subject_count = len(child_summaries)
    completed_subject_count = sum(1 for item in child_summaries if item.get("status") == "succeeded")
    blocked_subject_count = sum(1 for item in child_summaries if item.get("status") in {"blocked", "failed"})
    partial_subject_count = sum(1 for item in child_summaries if item.get("status") == "partial")
    status = _batch_status(child_responses)

    group_payload = {
        "summary_type": "native_preproc_group_summary",
        "batch": True,
        "project_id": project_id,
        "run_id": run_id,
        "created_at": _now_iso(),
        "subject_count": subject_count,
        "completed_subject_count": completed_subject_count,
        "partial_subject_count": partial_subject_count,
        "blocked_subject_count": blocked_subject_count,
        "subject_summaries": child_summaries,
        "status": status,
        "warnings": warnings,
    }
    if persist and group_summary_enabled:
        atomic_write_json(group_path, group_payload, schema_version=1)

    safety_flags = {
        "rawdata_readonly_confirmed": True,
        "no_external_tools_executed": True,
        "no_matlab_spm_dpabi": True,
        "research_use_only": True,
        "clinical_use_prohibited": True,
    }
    validation_payload = {
        "report_type": "native_preproc_batch_validation",
        "project_id": project_id,
        "run_id": run_id,
        "created_at": _now_iso(),
        "dry_run": dry_run,
        "status": status,
        "subject_count": subject_count,
        "subjects": child_summaries,
        "summary": {
            "completed_subject_count": completed_subject_count,
            "partial_subject_count": partial_subject_count,
            "blocked_subject_count": blocked_subject_count,
            "child_runs_total": len(child_runs),
            "artifact_count": sum(item.artifact_count for item in child_responses),
        },
        "warnings": warnings,
        "errors": [error for item in child_responses for error in item.errors],
        "safety_flags": safety_flags,
    }
    if persist:
        atomic_write_json(validation_path, validation_payload, schema_version=1)

    final_payload = {
        "report_type": "native_preproc_batch_final_report",
        "project_id": project_id,
        "run_id": run_id,
        "created_at": _now_iso(),
        "status": status,
        "subject_count": subject_count,
        "subject_summaries": child_summaries,
        "group_summary_path": str(group_path) if group_summary_enabled else "",
        "validation_report_path": str(validation_path),
        "limitations": [
            "Native Python pipeline only; MATLAB/SPM/DPABI/GPU were not executed.",
            "Clinical conclusions are prohibited.",
            *(
                ["Group summary is metadata-only and does not perform group statistical inference."]
                if group_summary_enabled
                else []
            ),
        ],
        "warnings": warnings,
    }
    if persist:
        atomic_write_json(final_path, final_payload, schema_version=1)

        if group_summary_enabled:
            stage_results.append(
                _report_stage(
                    "group_summary",
                    path=group_path,
                    artifact_type="group_summary",
                    warnings=["group_summary_is_metadata_only_no_group_statistical_model"],
                    result={"subject_count": subject_count},
                )
            )
        stage_results.append(
            _report_stage(
                "validation_report",
                path=validation_path,
                artifact_type="validation_report",
                result={"subject_count": subject_count},
            )
        )
        stage_results.append(
            _report_stage(
                "final_report",
                path=final_path,
                artifact_type="final_report",
                result={"subject_count": subject_count},
            )
        )

    response_warnings = list(warnings)
    registry_error = ""
    if persist:
        try:
            registry_path, registered_count = _register_native_output_artifacts(
                project_id=project_id,
                run_id=run_id,
                run_dir=run_dir,
                project_dir=project_dir,
                stage_results=stage_results,
            )
            response_warnings.append(
                f"Registered {registered_count} native output artifact(s) in {registry_path.name}."
            )
        except Exception as exc:
            registry_error = f"NATIVE_OUTPUT_REGISTRY_FAILED:{type(exc).__name__}"
            status = "partial" if status == "succeeded" else status

    if persist and registry_error:
        # The native validation/final reports are evidence sources.  Keep them
        # consistent with the returned lifecycle state if formal registration
        # failed after numerical files were produced.
        group_payload["status"] = status
        validation_payload["status"] = status
        validation_payload["errors"] = [
            *validation_payload["errors"],
            registry_error,
        ]
        final_payload["status"] = status
        final_payload["warnings"] = [
            *final_payload["warnings"],
            "Native output registration did not complete; do not treat this run as finished.",
        ]
        if group_summary_enabled:
            atomic_write_json(group_path, group_payload, schema_version=1)
        atomic_write_json(validation_path, validation_payload, schema_version=1)
        atomic_write_json(final_path, final_payload, schema_version=1)

    completed, blocked, failed, skipped, metadata_only, warning_stages = _stage_status_lists(stage_results)
    artifact_count = sum(item.artifact_count for item in child_responses) + sum(
        len(item.output_artifacts)
        for item in stage_results
        if item.stage_id in {"group_summary", "validation_report", "final_report"}
    )
    response = NativeFullPreprocResponse(
        ok=status in {"planned", "succeeded"},
        status=status,  # type: ignore[arg-type]
        dry_run=dry_run,
        project_id=project_id,
        run_id=run_id,
        run_dir=str(run_dir),
        backend="native_python",
        stage_graph=native_full_stage_graph_payload(),
        stage_results=stage_results,
        completed_stages=completed,
        blocked_stages=blocked,
        failed_stages=failed,
        skipped_stages=skipped,
        metadata_only_stages=metadata_only,
        warning_stages=warning_stages,
        artifact_count=artifact_count,
        manifest_path=str(manifest_path) if persist else "",
        validation_report_path=str(validation_path) if persist else "",
        final_report_path=str(final_path) if persist else "",
        warnings=response_warnings,
        errors=[
            *(error for item in child_responses for error in item.errors),
            *([registry_error] if registry_error else []),
        ],
        blocking_issues=[
            *(issue for item in child_responses for issue in item.blocking_issues),
            *(["NATIVE_OUTPUT_REGISTRY_FAILED"] if registry_error else []),
        ],
        next_actions=(
            ["Provide required atlas/template inputs and rerun before treating the batch as complete."]
            if status != "succeeded"
            else ["Review native validation and final reports before package export."]
        ),
        safety_flags=safety_flags,
        scheduler_mode=(resource_decision.scheduler_mode if resource_decision else "serial"),  # type: ignore[arg-type]
        worker_count_requested=(resource_decision.worker_count_requested if resource_decision else None),
        worker_count_calculated=(resource_decision.worker_count_calculated if resource_decision else 1),
        worker_count_used=(resource_decision.worker_count_used if resource_decision else 1),
        threads_per_worker_calculated=(resource_decision.threads_per_worker_calculated if resource_decision else 1),
        resource_decision=(resource_decision.as_dict() if resource_decision else {}),
        subject_execution=child_summaries,
        progress_url=f"/api/projects/{project_id}/preprocessing/native/runs/{run_id}/progress",
        started_at=(progress_now_iso() if resource_decision else ""),
        finished_at=(progress_now_iso() if resource_decision else ""),
    )
    if persist:
        atomic_write_json(manifest_path, response.model_dump(mode="json"), schema_version=1)
    return response


def _run_registered_batch(
    project_id: str,
    request: NativeFullPreprocRequest,
    *,
    project_dir: str,
    project_metadata: dict[str, object] | None,
    dry_run: bool,
    persist: bool = True,
) -> NativeFullPreprocResponse | None:
    subject_requests, warnings = _registered_subject_requests(
        request,
        project_metadata=project_metadata,
    )
    if not subject_requests:
        return None
    run_id, run_dir = _batch_run_dir(request, project_id=project_id, project_dir=project_dir)
    decision = plan_subject_execution(subject_requests, request.cpu_policy)
    warnings.append(
        "CPU scheduler decision: "
        f"mode={decision.scheduler_mode}, workers={decision.worker_count_used}, "
        f"threads_per_worker={decision.threads_per_worker_calculated}."
    )
    progress = initial_progress(
        project_id=project_id,
        run_id=run_id,
        run_dir=run_dir,
        subject_ids=[item.subject_id for item in subject_requests],
        resource_decision=decision.as_dict(),
    )
    if persist:
        run_dir.mkdir(parents=True, exist_ok=True)
        write_progress(run_dir, progress)

    child_runs: list[tuple[str, NativeFullPreprocResponse]] = []
    child_requests = [
        item.model_copy(
            update={
                "run_id": f"{run_id}_{item.subject_id}",
                # A child owns this directory and nothing at the batch root.
                "output_dir": str(run_dir / item.subject_id),
                "stage_overrides": {
                    **item.stage_overrides,
                    "group_summary": False,
                    "validation_report": False,
                    "final_report": False,
                },
            }
        )
        for item in subject_requests
    ]

    def mark_event(event: dict[str, Any]) -> None:
        subject_id = str(event.get("subject_id") or "")
        subject = progress["subjects"].get(subject_id)
        if not isinstance(subject, dict):
            return
        kind = str(event.get("kind") or "")
        now = str(event.get("at") or progress_now_iso())
        subject["heartbeat_at"] = now
        if kind == "subject_started":
            subject.update({"status": "running", "started_at": now, "worker_pid": event.get("worker_pid")})
            progress["status"] = "running"
        elif kind == "stage":
            subject.update({"stage_id": str(event.get("stage_id") or ""), "stage_status": str(event.get("stage_status") or "")})
        elif kind == "subject_finished":
            subject.update({"status": str(event.get("status") or "succeeded"), "finished_at": now})
        elif kind == "subject_failed":
            subject.update({"status": "failed", "error_message": str(event.get("error") or "worker failure"), "finished_at": now})
        if persist:
            write_progress(run_dir, progress)

    def failed_response(subject_id: str, error: str) -> NativeFullPreprocResponse:
        return NativeFullPreprocResponse(
            ok=False,
            status="failed",
            project_id=project_id,
            run_id=f"{run_id}_{subject_id}",
            run_dir=str(run_dir / subject_id),
            errors=[error],
            failed_stages=["subject_worker"],
            safety_flags={"rawdata_readonly_confirmed": True, "no_external_tools_executed": True},
        )

    # Dry-runs are intentionally kept in-process.  They create no numerical
    # artifacts and the resulting resource decision remains visible to users.
    if dry_run or decision.worker_count_used <= 1 or decision.scheduler_mode == "serial":
        for child_request in child_requests:
            subject_id = child_request.subject_id
            mark_event({"kind": "subject_started", "subject_id": subject_id, "at": progress_now_iso()})
            try:
                response = (
                    dry_run_native_full_preproc(project_id, child_request, project_dir=project_dir)
                    if dry_run
                    else execute_native_full_preproc(project_id, child_request, project_dir=project_dir)
                )
            except Exception as exc:  # preserve completed siblings
                response = failed_response(subject_id, str(exc))
            child_runs.append((subject_id, response))
            mark_event({"kind": "subject_finished", "subject_id": subject_id, "status": response.status, "at": progress_now_iso()})
    else:
        mp_context = multiprocessing.get_context("spawn")
        manager = multiprocessing.Manager()
        events = manager.Queue()
        try:
            with ProcessPoolExecutor(max_workers=decision.worker_count_used, mp_context=mp_context) as executor:
                futures = {
                    executor.submit(
                        execute_subject_worker,
                        {
                            "project_id": project_id,
                            "project_dir": project_dir,
                            "subject_id": child_request.subject_id,
                            "request": child_request.model_dump(mode="json"),
                            "threads_per_worker": decision.threads_per_worker_calculated,
                            "events": events,
                        },
                    ): child_request.subject_id
                    for child_request in child_requests
                }
                pending = set(futures)
                while pending:
                    done, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
                    while True:
                        try:
                            mark_event(events.get_nowait())
                        except Empty:
                            break
                    for future in done:
                        subject_id = futures[future]
                        try:
                            payload = future.result()
                            if payload.get("error"):
                                response = failed_response(subject_id, str(payload["error"]))
                            else:
                                response = NativeFullPreprocResponse.model_validate(payload["response"])
                        except Exception as exc:
                            response = failed_response(subject_id, f"Subject worker failed: {exc}")
                        child_runs.append((subject_id, response))
                        progress["completed_subjects"] = len(child_runs)
                        mark_event({"kind": "subject_finished", "subject_id": subject_id, "status": response.status, "at": progress_now_iso()})
        finally:
            manager.shutdown()
    child_runs.sort(key=lambda item: item[0])
    progress["completed_subjects"] = len(child_runs)
    progress["status"] = _batch_status([response for _, response in child_runs])
    progress["finished_at"] = progress_now_iso()
    if persist:
        write_progress(run_dir, progress)
    return _write_batch_response(
        project_id=project_id,
        run_id=run_id,
        run_dir=run_dir,
        project_dir=project_dir,
        dry_run=dry_run,
        child_runs=child_runs,
        warnings=warnings,
        resource_decision=decision,
        group_summary_enabled=bool(request.stage_overrides.get("group_summary", True)),
        persist=persist,
    )


def run_native_full_dry_run(
    project_id: str,
    request: NativeFullPreprocRequest,
    *,
    project_dir: str = "",
    project_metadata: dict[str, object] | None = None,
    persist_artifacts: bool = True,
) -> NativeFullPreprocResponse:
    batch_response = _run_registered_batch(
        project_id,
        request,
        project_dir=project_dir,
        project_metadata=project_metadata,
        dry_run=True,
        persist=persist_artifacts,
    )
    if batch_response is not None:
        return batch_response
    resolved, warnings = _request_with_registered_conversion_inputs(
        request,
        project_metadata=project_metadata,
    )
    response = dry_run_native_full_preproc(project_id, resolved, project_dir=project_dir)
    response.warnings = [*warnings, *response.warnings]
    return response


def run_native_full_execute(
    project_id: str,
    request: NativeFullPreprocRequest,
    *,
    project_dir: str = "",
    project_metadata: dict[str, object] | None = None,
) -> NativeFullPreprocResponse:
    batch_response = _run_registered_batch(
        project_id,
        request,
        project_dir=project_dir,
        project_metadata=project_metadata,
        dry_run=False,
    )
    if batch_response is not None:
        return batch_response
    resolved, warnings = _request_with_registered_conversion_inputs(
        request,
        project_metadata=project_metadata,
    )
    response = execute_native_full_preproc(project_id, resolved, project_dir=project_dir)
    response = response.model_copy(update={"warnings": [*warnings, *response.warnings]})
    return _persist_response_with_native_output_registry(
        response,
        project_id=project_id,
        project_dir=project_dir,
    )


def submit_native_full_execute(
    project_id: str,
    request: NativeFullPreprocRequest,
    *,
    project_dir: str = "",
    project_metadata: dict[str, object] | None = None,
) -> NativeFullPreprocResponse:
    """Persist a queued run and execute it outside the HTTP request lifecycle."""
    required = (
        "confirm_reviewed_native_execution",
        "confirm_rawdata_readonly",
        "confirm_no_external_tools",
        "confirm_research_use_only",
        "confirm_no_clinical_use",
    )
    missing = [name for name in required if not bool(getattr(request.confirmations, name))]
    if missing:
        return NativeFullPreprocResponse(
            ok=False,
            status="blocked",
            project_id=project_id,
            blocking_issues=[f"Missing required native execution confirmation: {name}" for name in missing],
            safety_flags={"rawdata_not_modified": True, "no_external_tools_executed": True},
        )
    run_id = request.run_id or _batch_run_id(project_id)
    queued_request = request.model_copy(update={"run_id": run_id})
    subjects, warnings = _registered_subject_requests(queued_request, project_metadata=project_metadata)
    if not subjects:
        subjects = [queued_request]
    run_dir = _batch_run_dir(queued_request, project_id=project_id, project_dir=project_dir)[1]
    decision = plan_subject_execution(subjects, queued_request.cpu_policy)
    run_dir.mkdir(parents=True, exist_ok=True)
    progress = initial_progress(
        project_id=project_id,
        run_id=run_id,
        run_dir=run_dir,
        subject_ids=[item.subject_id or "single_subject" for item in subjects],
        resource_decision=decision.as_dict(),
    )
    write_progress(run_dir, progress)
    response = NativeFullPreprocResponse(
        ok=True,
        status="queued",
        project_id=project_id,
        run_id=run_id,
        run_dir=str(run_dir),
        warnings=[*warnings, "Native preprocessing was queued for background execution."],
        safety_flags={"rawdata_readonly_confirmed": True, "no_external_tools_executed": True, "no_matlab_spm_dpabi": True},
        scheduler_mode=queued_request.cpu_policy.mode,
        worker_count_requested=decision.worker_count_requested,
        worker_count_calculated=decision.worker_count_calculated,
        worker_count_used=decision.worker_count_used,
        threads_per_worker_calculated=decision.threads_per_worker_calculated,
        resource_decision=decision.as_dict(),
        progress_url=f"/api/projects/{project_id}/preprocessing/native/runs/{run_id}/progress",
        started_at=str(progress["started_at"]),
    )
    atomic_write_json(run_dir / "native_full_run_manifest.json", response.model_dump(mode="json"), schema_version=1)

    def run_background() -> None:
        try:
            run_native_full_execute(project_id, queued_request, project_dir=project_dir, project_metadata=project_metadata)
        except Exception as exc:
            failed = response.model_copy(update={
                "ok": False,
                "status": "failed",
                "errors": [str(exc)],
                "finished_at": progress_now_iso(),
            })
            atomic_write_json(run_dir / "native_full_run_manifest.json", failed.model_dump(mode="json"), schema_version=1)
            progress["status"] = "failed"
            progress["errors"] = [str(exc)]
            progress["finished_at"] = progress_now_iso()
            write_progress(run_dir, progress)

    Thread(target=run_background, name=f"native-preproc-{run_id}", daemon=True).start()
    return response


def get_native_full_run(
    project_id: str,
    run_id: str,
    *,
    project_dir: str = "",
) -> NativeFullPreprocResponse:
    response = load_native_full_run_manifest(project_id, run_id, project_dir=project_dir)
    if response.status not in {"queued", "running", "cancel_requested"} or not response.run_dir:
        return response
    progress = load_progress(Path(response.run_dir))
    if not progress:
        return response
    status = str(progress.get("status") or response.status)
    heartbeat = str(progress.get("heartbeat_at") or "")
    try:
        stale = datetime.now(UTC) - datetime.fromisoformat(heartbeat) > timedelta(minutes=2)
    except ValueError:
        stale = False
    if status in {"queued", "running", "cancel_requested"} and stale:
        status = "interrupted"
        progress["status"] = status
        progress["finished_at"] = progress_now_iso()
        progress.setdefault("errors", []).append("Native preprocessing heartbeat became stale; run marked interrupted.")
        write_progress(Path(response.run_dir), progress)
    if status in {"interrupted", "cancelled", "failed", "partial", "succeeded"}:
        return response.model_copy(
            update={
                "ok": status == "succeeded",
                "status": status,
                "errors": list(progress.get("errors") or response.errors),
                "finished_at": str(progress.get("finished_at") or response.finished_at),
            }
        )
    return response


def get_native_full_progress(
    project_id: str,
    run_id: str,
    *,
    project_dir: str = "",
) -> dict[str, object]:
    run = get_native_full_run(project_id, run_id, project_dir=project_dir)
    progress = load_progress(Path(run.run_dir)) if run.run_dir else None
    if progress is None:
        return {
            "project_id": project_id,
            "run_id": run_id,
            "status": run.status,
            "available": False,
            "message": "No persisted native preprocessing progress snapshot found.",
        }
    return {"available": True, **progress}


def get_latest_native_full_run(
    project_id: str,
    *,
    project_dir: str = "",
) -> NativeFullPreprocResponse:
    root = Path(project_dir).resolve() if project_dir else Path("outputs") / "projects" / project_id
    native_root = root / "preprocessing_native_runs"
    manifests = (
        sorted(
            native_root.glob("*/native_full_run_manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if native_root.is_dir()
        else []
    )
    for manifest_path in manifests:
        run_id = manifest_path.parent.name
        response = load_native_full_run_manifest(project_id, run_id, project_dir=project_dir)
        if response.project_id in {"", project_id}:
            return response
    return NativeFullPreprocResponse(
        ok=False,
        status="blocked",
        project_id=project_id,
        blocking_issues=["No native preprocessing run manifest found."],
        safety_flags={"rawdata_not_modified": True, "no_external_tools_executed": True},
    )


def get_native_full_validation(
    project_id: str,
    run_id: str,
    *,
    project_dir: str = "",
) -> dict[str, object]:
    run = get_native_full_run(project_id, run_id, project_dir=project_dir)
    return {
        "ok": run.status not in {"failed", "blocked"},
        "project_id": project_id,
        "run_id": run_id,
        "status": run.status,
        "validation_report_path": run.validation_report_path,
        "blocked_stages": run.blocked_stages,
        "failed_stages": run.failed_stages,
        "warning_stages": run.warning_stages,
        "metadata_only_stages": run.metadata_only_stages,
        "safety_flags": run.safety_flags,
    }


def get_native_full_report(
    project_id: str,
    run_id: str,
    *,
    project_dir: str = "",
) -> dict[str, object]:
    run = get_native_full_run(project_id, run_id, project_dir=project_dir)
    return {
        "ok": bool(run.final_report_path),
        "project_id": project_id,
        "run_id": run_id,
        "status": run.status,
        "final_report_path": run.final_report_path,
        "manifest_path": run.manifest_path,
        "artifact_count": run.artifact_count,
        "completed_stages": run.completed_stages,
        "blocked_stages": run.blocked_stages,
        "failed_stages": run.failed_stages,
        "safety_flags": run.safety_flags,
    }


__all__ = [
    "get_latest_native_full_run",
    "get_native_full_report",
    "get_native_full_progress",
    "get_native_full_run",
    "get_native_full_validation",
    "run_native_full_dry_run",
    "run_native_full_execute",
    "submit_native_full_execute",
]
