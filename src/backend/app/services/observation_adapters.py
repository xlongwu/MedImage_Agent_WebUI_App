"""Read-only adapters used by the unified Observation collector."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.desktop import ProjectDetail, RunLinkRecord
from src.backend.app.schemas.observation import (
    ArtifactObservation,
    NodeObservation,
    ObservationLogFact,
    ObservationSourceRef,
    PipelineObservation,
    ValidationObservation,
)
from src.backend.app.services.preprocessing_artifact_registry import REGISTRY_FILENAME
from src.backend.app.services.preprocessing_pipeline_validation import (
    _RELOAD_REQUIRED_TYPES,
    validate_preprocessing_pipeline,
)
from src.backend.app.services.run_artifact_discovery import discover_run_artifacts
from src.backend.app.services.run_summary_preview import (
    load_run_summary_preview,
    resolve_run_summary_path,
)

_LOG_SUFFIXES = {".log", ".txt", ".err", ".out"}
_MAX_LOG_FILES = 20
_MAX_LOG_BYTES = 16_384
_MAX_FACT_CHARS = 500
_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/][^\s]+|/(?:home|Users|var|tmp|data)/[^\s]+)")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|password|secret|authorization)\s*[:=]\s*[^\s,;]+"
)


@dataclass
class AdaptedObservationFacts:
    sources: list[ObservationSourceRef] = field(default_factory=list)
    pipeline: PipelineObservation = field(default_factory=PipelineObservation)
    nodes: list[NodeObservation] = field(default_factory=list)
    artifacts: list[ArtifactObservation] = field(default_factory=list)
    validations: list[ValidationObservation] = field(default_factory=list)
    log_facts: list[ObservationLogFact] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blocking_facts: list[str] = field(default_factory=list)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_dir(project: ProjectDetail) -> Path:
    value = project.metadata.get("project_dir") if isinstance(project.metadata, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OBSERVATION_PROJECT_DIR_REQUIRED")
    root = Path(value).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("OBSERVATION_PROJECT_DIR_INVALID")
    return root


def _rawdata_roots(project: ProjectDetail) -> tuple[Path, ...]:
    values: list[str] = []
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    for key in ("rawdata_dir", "bids_dir", "dicom_dir"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value)
    return tuple(Path(value).expanduser().resolve() for value in values)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_project_path(
    value: str | Path,
    *,
    project_root: Path,
    rawdata_roots: Iterable[Path],
) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, project_root):
        raise ValueError("OBSERVATION_PATH_OUTSIDE_PROJECT")
    if any(_is_relative_to(resolved, root) for root in rawdata_roots):
        raise ValueError("OBSERVATION_RAWDATA_READ_REJECTED")
    return resolved


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _freshness(modified_at: datetime | None, run_created_at: datetime | None) -> str:
    if modified_at is None or run_created_at is None:
        return "unknown"
    return "fresh" if modified_at >= run_created_at else "stale"


def _source(
    *,
    source_type: str,
    observed_at: datetime,
    read_status: str,
    run_created_at: datetime | None,
    path: Path | None = None,
    project_root: Path | None = None,
    record_id: str | None = None,
    content_hash: str | None = None,
    warnings: Iterable[str] = (),
) -> ObservationSourceRef:
    modified_at: datetime | None = None
    relative_path: str | None = None
    if path is not None and path.exists():
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if project_root is not None:
            relative_path = _relative(path, project_root)
    fingerprint = {
        "source_type": source_type,
        "record_id": record_id,
        "relative_path": relative_path,
        "content_hash": content_hash,
        "read_status": read_status,
    }
    return ObservationSourceRef(
        source_id=f"source_{stable_hash(fingerprint)[:20]}",
        source_type=source_type,
        record_id=record_id,
        relative_path=relative_path,
        content_hash=content_hash,
        read_status=read_status,
        observed_at=observed_at,
        modified_at=modified_at,
        freshness=_freshness(modified_at, run_created_at),
        warnings=tuple(
            dict.fromkeys(_redact(item)[0] for item in warnings if item)
        ),
        redacted=True,
    )


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value if item is not None and str(item))
    return ()


def _redact(message: object) -> tuple[str, tuple[str, ...]]:
    text = str(message or "").replace("\r", " ").replace("\n", " ")
    flags: list[str] = []
    redacted = _SECRET_RE.sub("[REDACTED_SECRET]", text)
    if redacted != text:
        flags.append("secret")
    text = _PATH_RE.sub("[REDACTED_PATH]", redacted)
    if text != redacted:
        flags.append("path")
    if len(text) > _MAX_FACT_CHARS:
        text = text[:_MAX_FACT_CHARS] + "…"
        flags.append("truncated")
    return text, tuple(flags)


def _status_counts(nodes: list[NodeObservation]) -> tuple[int, int, int]:
    succeeded = sum(node.status.upper() in {"SUCCESS", "COMPLETED", "PASSED"} for node in nodes)
    failed = sum(node.status.upper() in {"FAILED", "ERROR"} for node in nodes)
    skipped = sum(node.status.upper() in {"SKIPPED", "CANCELLED"} for node in nodes)
    return succeeded, failed, skipped


def _artifact_type_from_path(path: Path) -> str:
    name = path.name.lower()
    mappings = (
        ("fisher", "fisher_z_matrix"),
        ("fc", "fc_matrix"),
        ("falff", "falff_map"),
        ("alff", "alff_map"),
        ("reho", "reho_map"),
        ("roi", "roi_timeseries"),
        ("atlas", "atlas"),
        ("provenance", "provenance_json"),
        ("manifest", "stage_manifest"),
    )
    for marker, artifact_type in mappings:
        if marker in name:
            return artifact_type
    if name.endswith((".nii", ".nii.gz", ".npy")):
        return "numerical_artifact"
    if name.endswith(".json"):
        return "json"
    if name.endswith((".tsv", ".csv")):
        return "table"
    return "file"


def _inspect_reload(path: Path, artifact_type: str) -> tuple[str, str | None, tuple[int, ...], str | None]:
    reload_required = artifact_type in _RELOAD_REQUIRED_TYPES or path.suffix.lower() == ".npy" or path.name.lower().endswith((".nii", ".nii.gz"))
    if not reload_required:
        return "not_required", None, (), None
    try:
        lower = path.name.lower()
        if lower.endswith((".nii", ".nii.gz")):
            import nibabel as nib

            image = nib.load(str(path))
            return "passed", "nifti_reload_ok", tuple(int(item) for item in image.shape), str(image.get_data_dtype())
        if path.suffix.lower() == ".npy":
            import numpy as np

            array = np.load(path, mmap_mode="r", allow_pickle=False)
            return "passed", "npy_reload_ok", tuple(int(item) for item in array.shape), str(array.dtype)
        if path.suffix.lower() in {".tsv", ".csv"}:
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
            return ("passed", "table_reload_ok", (), "text") if first else ("failed", "table_empty", (), "text")
    except Exception as exc:
        return "failed", f"reload_failed:{type(exc).__name__}", (), None
    return "unknown", "reload_handler_missing", (), None


def _registry_candidates(project_root: Path, run_id: str) -> tuple[Path, ...]:
    return (
        project_root / "preprocessing_runs" / run_id / REGISTRY_FILENAME,
        project_root / "work" / "pipeline_runs" / run_id / REGISTRY_FILENAME,
    )


def _native_validation_report_path(project_root: Path, run_id: str) -> Path:
    return (
        project_root
        / "preprocessing_native_runs"
        / run_id
        / "artifacts"
        / "validation_report"
        / "native_preproc_validation_report.json"
    )


def _native_progress_path(project_root: Path, run_id: str) -> Path:
    return (
        project_root
        / "preprocessing_native_runs"
        / run_id
        / "native_full_progress.json"
    )


def _summary_alias(
    summary: dict[str, Any],
    primary: str,
    alias: str,
) -> object:
    value = summary.get(primary)
    return value if value is not None else summary.get(alias)


def _artifact_limitation_flags(registry_item: dict[str, Any] | None) -> tuple[str, ...]:
    item = registry_item or {}
    flags = {
        flag
        for flag in ("metadata_only", "preview_only", "partial", "simplified")
        if bool(item.get(flag))
    }
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for key in ("stage_status", "capability_level"):
        value = str(metadata.get(key) or "").strip().lower()
        if value in {"metadata_only", "preview_only", "partial", "simplified"}:
            flags.add(value)
    return tuple(sorted(flags))


def adapt_observation_sources(
    project: ProjectDetail,
    run_link: RunLinkRecord,
    *,
    collected_at: datetime,
) -> AdaptedObservationFacts:
    facts = AdaptedObservationFacts()
    project_root = _project_dir(project)
    raw_roots = _rawdata_roots(project)
    run_created_at = _parse_datetime(run_link.created_at)

    # Pipeline summary.
    summary_path, summary_path_warnings = resolve_run_summary_path(project, run_link)
    summary, summary_warnings, summary_error = load_run_summary_preview(project, run_link)
    source_warnings = [*summary_path_warnings, *summary_warnings]
    if summary_error:
        source_warnings.append(summary_error)
    if summary_path is None or summary is None:
        facts.missing_sources.append("pipeline_summary")
        if any(
            marker in warning
            for warning in source_warnings
            for marker in (
                "OUTSIDE_PROJECT_OUTPUTS",
                "IN_RAWDATA_REJECTED",
                "SUMMARY_PATH_INVALID",
                "SUMMARY_PATH_REJECTED",
            )
        ):
            facts.blocking_facts.append("PIPELINE_SUMMARY_PATH_REJECTED")
        facts.sources.append(
            _source(
                source_type="pipeline_summary",
                observed_at=collected_at,
                read_status="invalid" if summary_error else "missing",
                run_created_at=run_created_at,
                record_id=run_link.run_id,
                warnings=source_warnings,
            )
        )
    else:
        safe_summary = _safe_project_path(
            summary_path, project_root=project_root, rawdata_roots=raw_roots
        )
        summary_source = _source(
            source_type="pipeline_summary",
            observed_at=collected_at,
            read_status="ok",
            run_created_at=run_created_at,
            path=safe_summary,
            project_root=project_root,
            record_id=run_link.run_id,
            content_hash=_sha256_file(safe_summary),
            warnings=source_warnings,
        )
        facts.sources.append(summary_source)
        facts.pipeline = PipelineObservation(
            status=str(summary.get("status") or "UNKNOWN").upper(),
            started_at=_parse_datetime(summary.get("started_at")),
            ended_at=_parse_datetime(
                _summary_alias(summary, "finished_at", "ended_at")
            ),
            nodes_total=summary.get("nodes_total"),
            nodes_succeeded=_summary_alias(
                summary, "nodes_succeeded", "nodes_success"
            ),
            nodes_failed=_summary_alias(summary, "nodes_failed", "nodes_failure"),
            nodes_skipped=_summary_alias(summary, "nodes_skipped", "nodes_skip"),
            errors=tuple(_redact(item)[0] for item in (summary.get("errors") or [])),
            warnings=tuple(_redact(item)[0] for item in (summary.get("warnings") or [])),
            evidence_ids=(summary_source.source_id,),
        )

    # Node state ledger. Symlink resolution is checked before reading.
    configured_state_root = run_link.payload.get("state_root") if isinstance(run_link.payload, dict) else None
    if isinstance(configured_state_root, str) and configured_state_root.strip():
        try:
            state_base = _safe_project_path(
                configured_state_root,
                project_root=project_root,
                rawdata_roots=raw_roots,
            )
        except ValueError:
            facts.blocking_facts.append("RECOVERY_STATE_ROOT_REJECTED")
            state_base = project_root / "__rejected_recovery_state_root__"
    else:
        state_base = project_root / "work"
    states_root = (state_base / "states" / run_link.run_id).resolve()
    state_paths: list[Path] = []
    if _is_relative_to(states_root, project_root) and states_root.exists():
        state_paths = sorted(states_root.rglob("*.json"))
    if not state_paths:
        facts.missing_sources.append("node_states")
        facts.sources.append(
            _source(
                source_type="node_states",
                observed_at=collected_at,
                read_status="missing",
                run_created_at=run_created_at,
                record_id=run_link.run_id,
                warnings=("NODE_STATES_MISSING",),
            )
        )
    for state_path in state_paths:
        try:
            safe_state = _safe_project_path(
                state_path, project_root=project_root, rawdata_roots=raw_roots
            )
            payload = json.loads(safe_state.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("top_level_not_object")
        except Exception as exc:
            facts.blocking_facts.append(f"NODE_STATE_INVALID:{state_path.name}")
            facts.sources.append(
                _source(
                    source_type="node_state",
                    observed_at=collected_at,
                    read_status="invalid",
                    run_created_at=run_created_at,
                    record_id=state_path.name,
                    warnings=(f"NODE_STATE_READ_FAILED:{type(exc).__name__}",),
                )
            )
            continue
        state_source = _source(
            source_type="node_state",
            observed_at=collected_at,
            read_status="ok",
            run_created_at=run_created_at,
            path=safe_state,
            project_root=project_root,
            record_id=str(payload.get("node") or safe_state.stem),
            content_hash=_sha256_file(safe_state),
        )
        facts.sources.append(state_source)
        raw_outputs = payload.get("outputs")
        outputs: list[str] = []
        for raw_output in _strings(raw_outputs):
            try:
                resolved = _safe_project_path(
                    raw_output, project_root=project_root, rawdata_roots=raw_roots
                )
                outputs.append(_relative(resolved, project_root))
            except ValueError:
                facts.blocking_facts.append("NODE_OUTPUT_PATH_REJECTED")
        node = NodeObservation(
            node_id=str(payload.get("node") or payload.get("node_id") or safe_state.stem),
            subject_id=str(payload.get("subject") or "project"),
            session_id=str(payload.get("session")) if payload.get("session") else None,
            status=str(payload.get("status") or "UNKNOWN").upper(),
            attempt=int(payload.get("attempt") or 0),
            backend=str(payload.get("backend")) if payload.get("backend") else None,
            contract_version=str(payload.get("contract_version")) if payload.get("contract_version") else None,
            outputs=tuple(outputs),
            errors=_strings(payload.get("errors")),
            warnings=_strings(payload.get("warnings")),
            evidence_ids=(state_source.source_id,),
        )
        facts.nodes.append(node)
        for level, messages in (("error", node.errors), ("warning", node.warnings)):
            for index, message in enumerate(messages):
                redacted, redaction_flags = _redact(message)
                facts.log_facts.append(
                    ObservationLogFact(
                        fact_id=f"fact_{stable_hash({'source': state_source.source_id, 'level': level, 'index': index, 'message': redacted})[:20]}",
                        level=level,
                        source_id=state_source.source_id,
                        message=redacted,
                        node_id=node.node_id,
                        subject_id=node.subject_id,
                        redaction_flags=redaction_flags,
                    )
                )

    if facts.nodes:
        succeeded, failed, skipped = _status_counts(facts.nodes)
        expected = facts.pipeline.nodes_total
        consistent = expected is None or expected == len(facts.nodes)
        if facts.pipeline.nodes_succeeded is not None:
            consistent = consistent and facts.pipeline.nodes_succeeded == succeeded
        if facts.pipeline.nodes_failed is not None:
            consistent = consistent and facts.pipeline.nodes_failed == failed
        if not consistent:
            facts.conflicts.append("PIPELINE_NODE_STATE_COUNT_CONFLICT")
        facts.pipeline = facts.pipeline.model_copy(
            update={
                "nodes_total": expected if expected is not None else len(facts.nodes),
                "nodes_succeeded": facts.pipeline.nodes_succeeded if facts.pipeline.nodes_succeeded is not None else succeeded,
                "nodes_failed": facts.pipeline.nodes_failed if facts.pipeline.nodes_failed is not None else failed,
                "nodes_skipped": facts.pipeline.nodes_skipped if facts.pipeline.nodes_skipped is not None else skipped,
                "active_nodes": sum(node.status in {"RUNNING", "PENDING"} for node in facts.nodes),
                "summary_consistent": consistent,
            }
        )

    # The native orchestrator owns subject-level progress separately from the
    # project-level Pipeline Runtime node state. Project node counts remain
    # bound to the runtime summary; these additional observations exist only
    # to project the reviewed subject scope truthfully.
    native_progress_path = _native_progress_path(project_root, run_link.run_id)
    if native_progress_path.exists():
        try:
            safe_progress = _safe_project_path(
                native_progress_path,
                project_root=project_root,
                rawdata_roots=raw_roots,
            )
            progress_payload = json.loads(
                safe_progress.read_text(encoding="utf-8")
            )
            if not isinstance(progress_payload, dict):
                raise ValueError("native_progress_invalid")
            subjects_payload = progress_payload.get("subjects")
            if not isinstance(subjects_payload, dict):
                raise ValueError("native_progress_invalid")
        except Exception as exc:
            facts.blocking_facts.append("NATIVE_PROGRESS_INVALID")
            facts.sources.append(
                _source(
                    source_type="native_progress",
                    observed_at=collected_at,
                    read_status="invalid",
                    run_created_at=run_created_at,
                    record_id=run_link.run_id,
                    warnings=(
                        f"NATIVE_PROGRESS_READ_FAILED:{type(exc).__name__}",
                    ),
                )
            )
        else:
            progress_source = _source(
                source_type="native_progress",
                observed_at=collected_at,
                read_status="ok",
                run_created_at=run_created_at,
                path=safe_progress,
                project_root=project_root,
                record_id=run_link.run_id,
                content_hash=_sha256_file(safe_progress),
            )
            facts.sources.append(progress_source)
            subject_nodes: list[NodeObservation] = []
            for subject_id, subject_payload in subjects_payload.items():
                if not isinstance(subject_payload, dict) or not str(subject_id):
                    facts.conflicts.append("NATIVE_PROGRESS_SUBJECT_INVALID")
                    continue
                subject_nodes.append(
                    NodeObservation(
                        node_id="native_preproc_subject",
                        subject_id=str(subject_id),
                        status=str(
                            subject_payload.get("status") or "UNKNOWN"
                        ).upper(),
                        attempt=1,
                        backend="native_python",
                        warnings=_strings(subject_payload.get("warnings")),
                        errors=_strings(subject_payload.get("errors")),
                        evidence_ids=(progress_source.source_id,),
                    )
                )
            completed = sum(
                node.status in {"SUCCESS", "SUCCEEDED", "COMPLETED"}
                for node in subject_nodes
            )
            expected_total = progress_payload.get("total_subjects")
            expected_completed = progress_payload.get("completed_subjects")
            if (
                isinstance(expected_total, int)
                and expected_total != len(subject_nodes)
            ) or (
                isinstance(expected_completed, int)
                and expected_completed != completed
            ):
                facts.conflicts.append(
                    "NATIVE_PROGRESS_SUBJECT_COUNT_CONFLICT"
                )
            facts.nodes.extend(subject_nodes)

    # Artifact registry is the authority for registration/provenance metadata.
    registry_by_path: dict[str, dict[str, Any]] = {}
    registry_artifacts: list[dict[str, Any]] = []
    for registry_path in _registry_candidates(project_root, run_link.run_id):
        if not registry_path.exists():
            continue
        try:
            safe_registry = _safe_project_path(
                registry_path, project_root=project_root, rawdata_roots=raw_roots
            )
            registry_payload = json.loads(safe_registry.read_text(encoding="utf-8"))
            registry_artifacts = registry_payload.get("artifacts", [])
            if not isinstance(registry_artifacts, list):
                raise ValueError("artifacts_not_list")
        except Exception as exc:
            facts.blocking_facts.append("ARTIFACT_REGISTRY_INVALID")
            facts.sources.append(
                _source(
                    source_type="artifact_registry",
                    observed_at=collected_at,
                    read_status="invalid",
                    run_created_at=run_created_at,
                    record_id=run_link.run_id,
                    warnings=(f"ARTIFACT_REGISTRY_READ_FAILED:{type(exc).__name__}",),
                )
            )
            continue
        registry_source = _source(
            source_type="artifact_registry",
            observed_at=collected_at,
            read_status="ok",
            run_created_at=run_created_at,
            path=safe_registry,
            project_root=project_root,
            record_id=run_link.run_id,
            content_hash=_sha256_file(safe_registry),
        )
        facts.sources.append(registry_source)
        for item in registry_artifacts:
            if not isinstance(item, dict):
                continue
            raw_path = str(item.get("path") or "")
            if not raw_path:
                continue
            if item.get("path_kind") == "project_relative":
                raw_path = str(project_root / raw_path)
            try:
                resolved = _safe_project_path(
                    raw_path, project_root=project_root, rawdata_roots=raw_roots
                )
            except ValueError:
                facts.blocking_facts.append("ARTIFACT_REGISTRY_PATH_REJECTED")
                continue
            registry_by_path[str(resolved).casefold()] = {**item, "_source_id": registry_source.source_id}

    discovered: list[dict[str, Any]] = []
    artifact_warnings: list[str] = []
    try:
        discovered, artifact_warnings = discover_run_artifacts(project, run_link)
    except Exception as exc:
        artifact_warnings.append(f"ARTIFACT_DISCOVERY_FAILED:{type(exc).__name__}")
    candidate_paths: dict[str, tuple[Path, dict[str, Any] | None, dict[str, Any] | None]] = {}
    for item in discovered:
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            path = _safe_project_path(raw_path, project_root=project_root, rawdata_roots=raw_roots)
        except ValueError:
            # The reviewed pipeline is a control-plane evidence link.  It can
            # live in the shared reviewed-pipeline directory, but must never be
            # mistaken for a project-owned scientific artifact.  All other
            # rejected discovery paths remain a blocking safety fact.
            if item.get("source") == "run_link.pipeline_path":
                continue
            facts.blocking_facts.append("ARTIFACT_PATH_REJECTED")
            continue
        candidate_paths[str(path).casefold()] = (path, item, registry_by_path.get(str(path).casefold()))
    for key, registry_item in registry_by_path.items():
        path = Path(str(registry_item.get("path") or ""))
        if registry_item.get("path_kind") == "project_relative":
            path = project_root / path
        path = path.resolve()
        candidate_paths.setdefault(key, (path, None, registry_item))

    if not candidate_paths:
        facts.missing_sources.append("artifacts")
    artifact_source = _source(
        source_type="artifact_discovery",
        observed_at=collected_at,
        read_status="ok" if candidate_paths else "missing",
        run_created_at=run_created_at,
        record_id=run_link.run_id,
        content_hash=stable_hash(sorted(candidate_paths)),
        warnings=artifact_warnings,
    )
    facts.sources.append(artifact_source)
    registry_by_id = {
        str(item.get("artifact_id")): item
        for item in registry_artifacts
        if isinstance(item, dict) and item.get("artifact_id")
    }
    for path, discovered_item, registry_item in candidate_paths.values():
        exists = path.exists() and path.is_file()
        artifact_type = str((registry_item or {}).get("artifact_type") or _artifact_type_from_path(path))
        reload_status, reload_message, shape, dtype = (
            _inspect_reload(path, artifact_type) if exists else ("failed", "artifact_missing", (), None)
        )
        registry_shape = (registry_item or {}).get("shape")
        if not shape and isinstance(registry_shape, list):
            shape = tuple(int(item) for item in registry_shape if isinstance(item, int))
        dtype = dtype or (str((registry_item or {}).get("dtype")) if (registry_item or {}).get("dtype") else None)
        flags = _artifact_limitation_flags(registry_item)
        provenance = (
            (registry_item or {}).get("provenance_id")
            or (registry_item or {}).get("provenance_path")
            or (registry_item or {}).get("provenance")
            or (registry_item or {}).get("source_artifact_ids")
        )
        source_ids = tuple(
            str(item)
            for item in (registry_item or {}).get("source_artifact_ids", [])
            if str(item)
        )
        declared_input_hashes = (
            ((registry_item or {}).get("metadata") or {}).get("input_hashes") or []
        )
        input_hashes = tuple(
            sorted(
                {
                    str(registry_by_id[source_id].get("checksum") or "")
                    for source_id in source_ids
                    if source_id in registry_by_id
                    and str(registry_by_id[source_id].get("checksum") or "")
                }
                | {str(value) for value in declared_input_hashes if str(value)}
            )
        )
        parameter_hash = str(
            ((registry_item or {}).get("metadata") or {}).get("parameter_hash")
            or ((registry_item or {}).get("metadata") or {}).get("params_hash")
            or ""
        ) or None
        provenance_value = str((registry_item or {}).get("provenance_path") or "")
        if parameter_hash is None and provenance_value:
            provenance_path = Path(provenance_value)
            if not provenance_path.is_absolute():
                provenance_path = project_root / provenance_path
            try:
                provenance_payload = json.loads(
                    provenance_path.read_text(encoding="utf-8")
                )
                parameters = (
                    provenance_payload.get("parameters")
                    or provenance_payload.get("params")
                    or provenance_payload.get("parameter_snapshot")
                )
                if isinstance(parameters, dict) and parameters:
                    parameter_hash = stable_hash(parameters)
            except Exception:
                parameter_hash = None
        evidence_ids = tuple(
            dict.fromkeys(
                item
                for item in (
                    artifact_source.source_id,
                    (registry_item or {}).get("_source_id"),
                )
                if isinstance(item, str) and item
            )
        )
        facts.artifacts.append(
            ArtifactObservation(
                artifact_id=str((registry_item or {}).get("artifact_id") or (discovered_item or {}).get("artifact_id") or f"artifact_{stable_hash(_relative(path, project_root))[:20]}"),
                artifact_type=artifact_type,
                owner_node_id=str((registry_item or {}).get("stage_id") or (discovered_item or {}).get("node_id") or "") or None,
                subject_id=str(
                    (registry_item or {}).get("subject_id")
                    or (registry_item or {}).get("subject")
                    or ""
                )
                or None,
                session_id=str(
                    (registry_item or {}).get("session_id")
                    or (registry_item or {}).get("session")
                    or ""
                )
                or None,
                relative_path=_relative(path, project_root),
                exists=exists,
                size_bytes=path.stat().st_size if exists else None,
                checksum_sha256=_sha256_file(path) if exists else None,
                input_hashes=input_hashes,
                parameter_hash=parameter_hash,
                shape=shape,
                dtype=dtype,
                reload_status=reload_status,
                reload_message=reload_message,
                provenance_id=str(provenance) if provenance else None,
                registration_status="registered" if registry_item else "unregistered",
                limitation_flags=flags,
                evidence_ids=evidence_ids,
            )
        )

    # Existing validation service is read-only and consumes the registry/artifacts.
    preprocessing_run = project_root / "preprocessing_runs" / run_link.run_id
    native_validation_report = _native_validation_report_path(project_root, run_link.run_id)
    if preprocessing_run.exists():
        response = validate_preprocessing_pipeline(
            project.id, run_link.run_id, project_dir=str(project_root)
        )
        payload = response.model_dump(mode="json")
        validation_hash = stable_hash(payload)
        validation_source = _source(
            source_type="validation",
            observed_at=collected_at,
            read_status="ok",
            run_created_at=run_created_at,
            record_id=run_link.run_id,
            content_hash=validation_hash,
            warnings=response.warnings,
        )
        facts.sources.append(validation_source)
        if response.errors or response.status == "blocked":
            status = "failed"
        elif response.status == "ready_for_review":
            status = "passed"
        elif response.status == "warning":
            status = "warning"
        else:
            status = "unknown"
        pipeline_status = facts.pipeline.status.upper()
        if (
            pipeline_status in {"SUCCESS", "COMPLETED"} and status == "failed"
        ) or (
            pipeline_status in {"FAILED", "ERROR"} and status == "passed"
        ):
            facts.conflicts.append("PIPELINE_VALIDATION_CONFLICT")
        facts.validations.append(
            ValidationObservation(
                validation_id=f"validation_{validation_hash[:20]}",
                validator_id="preprocessing_pipeline_validation",
                validator_version="1",
                scope=run_link.run_id,
                status=status,
                checks=tuple(str(item.get("stage_id")) for item in response.stage_summary if item.get("stage_id")),
                blocking_issues=tuple(response.errors),
                report_ref=None,
                report_hash=validation_hash,
                evidence_ids=(validation_source.source_id,),
            )
        )
    elif native_validation_report.exists():
        try:
            safe_report = _safe_project_path(
                native_validation_report,
                project_root=project_root,
                rawdata_roots=raw_roots,
            )
            payload = json.loads(safe_report.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("native_validation_not_object")
        except Exception as exc:
            facts.blocking_facts.append("NATIVE_VALIDATION_REPORT_INVALID")
            facts.sources.append(
                _source(
                    source_type="validation",
                    observed_at=collected_at,
                    read_status="invalid",
                    run_created_at=run_created_at,
                    record_id=run_link.run_id,
                    warnings=(f"NATIVE_VALIDATION_REPORT_READ_FAILED:{type(exc).__name__}",),
                )
            )
        else:
            errors = _strings(payload.get("errors"))
            status_text = str(payload.get("status") or "").lower()
            validation_status = (
                "passed"
                if status_text in {"succeeded", "success", "completed"} and not errors
                else "failed"
                if status_text in {"failed", "blocked", "partial", "cancelled"} or errors
                else "unknown"
            )
            validation_source = _source(
                source_type="validation",
                observed_at=collected_at,
                read_status="ok",
                run_created_at=run_created_at,
                path=safe_report,
                project_root=project_root,
                record_id=run_link.run_id,
                content_hash=_sha256_file(safe_report),
                warnings=_strings(payload.get("warnings")),
            )
            facts.sources.append(validation_source)
            facts.validations.append(
                ValidationObservation(
                    validation_id=f"native_validation_{stable_hash({'run_id': run_link.run_id, 'hash': validation_source.content_hash})[:20]}",
                    validator_id="native_full_preproc_validation",
                    validator_version="1",
                    scope=run_link.run_id,
                    status=validation_status,
                    checks=tuple(
                        str(item.get("stage_id"))
                        for item in payload.get("stage_results", [])
                        if isinstance(item, dict) and item.get("stage_id")
                    ),
                    blocking_issues=errors,
                    report_ref=_relative(safe_report, project_root),
                    report_hash=validation_source.content_hash,
                    evidence_ids=(validation_source.source_id,),
                )
            )
    elif (project_root / "preprocessing_native_runs" / run_link.run_id).exists():
        facts.missing_sources.append("validation")
        facts.sources.append(
            _source(
                source_type="validation",
                observed_at=collected_at,
                read_status="missing",
                run_created_at=run_created_at,
                record_id=run_link.run_id,
                warnings=("NATIVE_VALIDATION_REPORT_MISSING",),
            )
        )
    else:
        facts.missing_sources.append("validation")
        facts.sources.append(
            _source(
                source_type="validation",
                observed_at=collected_at,
                read_status="missing",
                run_created_at=run_created_at,
                record_id=run_link.run_id,
                warnings=("VALIDATION_EVIDENCE_MISSING",),
            )
        )

    # Bounded, allowlisted log excerpts. Full logs and paths are never persisted.
    log_candidates: list[Path] = []
    for root in (project_root / "logs", project_root / "work" / "logs"):
        root = root.resolve()
        if not root.exists() or not _is_relative_to(root, project_root):
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in _LOG_SUFFIXES and run_link.run_id in path.as_posix():
                log_candidates.append(path)
    if not log_candidates:
        facts.missing_sources.append("logs")
        facts.sources.append(
            _source(
                source_type="logs",
                observed_at=collected_at,
                read_status="missing",
                run_created_at=run_created_at,
                record_id=run_link.run_id,
                warnings=("LOG_SOURCE_MISSING",),
            )
        )
    for path in sorted(log_candidates)[:_MAX_LOG_FILES]:
        try:
            safe_log = _safe_project_path(path, project_root=project_root, rawdata_roots=raw_roots)
            raw = safe_log.read_bytes()[:_MAX_LOG_BYTES].decode("utf-8", errors="replace")
        except Exception as exc:
            facts.sources.append(
                _source(
                    source_type="log",
                    observed_at=collected_at,
                    read_status="invalid",
                    run_created_at=run_created_at,
                    record_id=path.name,
                    warnings=(f"LOG_READ_FAILED:{type(exc).__name__}",),
                )
            )
            continue
        log_source = _source(
            source_type="log",
            observed_at=collected_at,
            read_status="ok",
            run_created_at=run_created_at,
            path=safe_log,
            project_root=project_root,
            record_id=path.name,
            content_hash=_sha256_file(safe_log),
            warnings=("LOG_CONTENT_TRUNCATED",) if safe_log.stat().st_size > _MAX_LOG_BYTES else (),
        )
        facts.sources.append(log_source)
        for index, line in enumerate(raw.splitlines()):
            upper = line.upper()
            level = "error" if "ERROR" in upper or "FAILED" in upper else "warning" if "WARN" in upper else None
            if level is None:
                continue
            message, flags = _redact(line)
            facts.log_facts.append(
                ObservationLogFact(
                    fact_id=f"fact_{stable_hash({'source': log_source.source_id, 'index': index, 'message': message})[:20]}",
                    level=level,
                    source_id=log_source.source_id,
                    message=message,
                    redaction_flags=flags,
                )
            )

    stale_types = sorted({source.source_type for source in facts.sources if source.freshness == "stale"})
    if stale_types:
        facts.conflicts.append("STALE_SOURCES:" + ",".join(stale_types))
    return facts
