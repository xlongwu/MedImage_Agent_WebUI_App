"""Pure run artifact discovery helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from src.backend.app.schemas.desktop import ProjectDetail, RunLinkRecord
from src.backend.app.services.run_artifact_preview import json_preview_summary
from src.backend.app.services.run_summary_preview import recovery_run_output_roots
from src.backend.app.tools.artifact_utils import read_json_artifact

SUMMARY_WARNING_LIMIT = 50
ARTIFACT_PREVIEW_MAX_BYTES = 80_000
ARTIFACT_ERROR_EXCERPT_MAX_CHARS = 1_200
ARTIFACT_QC_METRIC_LIMIT = 12
PREVIEWABLE_SUFFIXES = {".json", ".txt", ".md", ".csv", ".tsv", ".log"}
PATH_SUFFIXES = {
    ".json",
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".log",
    ".yaml",
    ".yml",
    ".html",
    ".nii",
    ".nii.gz",
    ".mat",
    ".png",
    ".jpg",
    ".jpeg",
}
ARTIFACT_REGISTRY_FILENAME = "preprocessing_artifact_registry.json"


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


def _relative_to_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _project_summary_roots(project: ProjectDetail, record: RunLinkRecord) -> list[Path]:
    roots: list[Path] = []
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    candidates = [
        metadata.get("project_dir"),
        Path(record.project_config_path).parent if record.project_config_path else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        base = Path(str(candidate)).expanduser().resolve()
        roots.extend(
            [
                base / "work",
                base / "data",
                base / "reports",
                base / "logs",
                base / "derivatives",
                base / "preprocessing_runs",
                base / "preprocessing_native_runs",
            ]
        )
    roots.extend(recovery_run_output_roots(project, record))
    return _dedupe_paths(roots)


def _project_artifact_roots(project: ProjectDetail, record: RunLinkRecord) -> list[Path]:
    roots = _project_summary_roots(project, record)
    if record.pipeline_path:
        try:
            roots.append(Path(record.pipeline_path).expanduser().resolve().parent)
        except Exception:
            pass
    return _dedupe_paths(roots)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _rawdata_roots(project: ProjectDetail) -> list[Path]:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    rawdata_dir = metadata.get("rawdata_dir")
    if not rawdata_dir:
        return []
    return [Path(str(rawdata_dir)).expanduser().resolve()]


def _path_suffix(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".nii.gz"):
        return ".nii.gz"
    return path.suffix.lower()


def _artifact_kind(path: Path) -> str:
    suffix = _path_suffix(path)
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "markdown"
    if suffix == ".csv":
        return "csv"
    if suffix == ".log":
        return "log"
    if suffix == ".txt":
        return "text"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix in {".png", ".jpg", ".jpeg"}:
        return "image"
    if suffix == ".nii.gz" or suffix == ".nii":
        return "nifti"
    if suffix == ".mat":
        return "matlab"
    return "binary"


def _is_previewable(path: Path) -> bool:
    return _path_suffix(path) in PREVIEWABLE_SUFFIXES


def artifact_id_for_path(path: Path) -> str:
    return f"artifact_{hashlib.sha256(str(path).casefold().encode('utf-8')).hexdigest()[:16]}"


def _modified_at(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        UTC,
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _relative_artifact_path(path: Path, project: ProjectDetail, roots: list[Path]) -> str:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    project_dir = metadata.get("project_dir")
    if project_dir:
        try:
            return str(path.relative_to(Path(str(project_dir)).expanduser().resolve()))
        except ValueError:
            pass
    for root in roots:
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return path.name


def _resolve_summary_path(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> tuple[Path | None, list[str]]:
    if not record.summary_path:
        return None, ["SUMMARY_PATH_MISSING: run link has no summary_path."]

    try:
        target = Path(record.summary_path).expanduser().resolve()
    except Exception as exc:
        return None, [f"SUMMARY_PATH_INVALID: {exc}"]

    if target.suffix.lower() != ".json":
        return None, [f"SUMMARY_PATH_REJECTED: summary_path must be a JSON file: {target}"]

    if _relative_to_any(target, _rawdata_roots(project)):
        return None, [f"SUMMARY_PATH_IN_RAWDATA_REJECTED: {target}"]

    allowed_roots = _project_summary_roots(project, record)
    if not allowed_roots or not _relative_to_any(target, allowed_roots):
        return None, [f"SUMMARY_PATH_OUTSIDE_PROJECT_OUTPUTS: {target}"]

    if not target.exists():
        return None, [f"SUMMARY_FILE_MISSING: {target}"]
    if not target.is_file():
        return None, [f"SUMMARY_PATH_NOT_FILE: {target}"]
    return target, []


def _node_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    node_results = raw.get("node_results")
    if isinstance(node_results, list):
        return [item for item in node_results if isinstance(item, dict)]
    nodes = raw.get("nodes")
    if isinstance(nodes, list):
        return [item for item in nodes if isinstance(item, dict)]
    return []


def _load_summary_raw(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    target, warnings = _resolve_summary_path(project, record)
    if warnings:
        return None, warnings, None
    assert target is not None
    try:
        raw = read_json_artifact(target)
    except JSONDecodeError as exc:
        return None, [], f"SUMMARY_JSON_INVALID: {target}: {exc.msg}"
    except Exception as exc:
        return None, [], f"SUMMARY_READ_FAILED: {target}: {exc}"
    if not isinstance(raw, dict):
        return None, [], f"SUMMARY_JSON_INVALID: {target}: top-level JSON value must be an object"
    return raw, [], None


def _looks_like_artifact_path(value: str) -> bool:
    if not value or "\n" in value or "\r" in value:
        return False
    path = Path(value)
    if _path_suffix(path) in PATH_SUFFIXES:
        return True
    normalized = value.replace("\\", "/")
    return any(part in normalized for part in ("/reports/", "/work/", "/logs/", "/derivatives/"))


def _collect_path_candidates(value: Any) -> list[str]:
    candidates: list[str] = []
    if isinstance(value, str):
        if _looks_like_artifact_path(value):
            candidates.append(value)
        return candidates
    if isinstance(value, list):
        for item in value:
            candidates.extend(_collect_path_candidates(item))
        return candidates
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if isinstance(item, str) and (
                key_text.endswith("path")
                or key_text.endswith("_path")
                or key_text.endswith("log")
                or key_text in {"outputs", "artifacts"}
                or _looks_like_artifact_path(item)
            ):
                candidates.append(item)
            else:
                candidates.extend(_collect_path_candidates(item))
    return candidates


def _resolve_candidate_path(
    raw_path: str,
    project: ProjectDetail,
    record: RunLinkRecord,
    *,
    base_dirs: list[Path],
) -> tuple[Path | None, str | None]:
    try:
        candidate = Path(raw_path).expanduser()
    except Exception as exc:
        return None, f"ARTIFACT_PATH_INVALID: {raw_path}: {exc}"

    resolved_candidates: list[Path] = []
    if candidate.is_absolute():
        resolved_candidates.append(candidate.resolve())
    else:
        for base_dir in base_dirs:
            resolved_candidates.append((base_dir / candidate).resolve())

    allowed_roots = _project_artifact_roots(project, record)
    rawdata_roots = _rawdata_roots(project)
    last_reason = "ARTIFACT_PATH_OUTSIDE_PROJECT_OUTPUTS"
    for target in _dedupe_paths(resolved_candidates):
        if _relative_to_any(target, rawdata_roots):
            return None, f"ARTIFACT_PATH_IN_RAWDATA_REJECTED: {target}"
        if not allowed_roots or not _relative_to_any(target, allowed_roots):
            last_reason = f"ARTIFACT_PATH_OUTSIDE_PROJECT_OUTPUTS: {target}"
            continue
        return target, None
    return None, last_reason


def _node_id_from_source(source: str, path: Path) -> str | None:
    if source.startswith("node_state:"):
        return source.removeprefix("node_state:").removesuffix(".json") or None
    normalized = str(path).replace("\\", "/")
    parts = normalized.split("/")
    if "states" in parts:
        index = parts.index("states")
        if len(parts) > index + 2:
            return Path(parts[index + 2]).stem
    return None


def _artifact_text_for_matching(path: Path, source: str) -> str:
    return f"{path.name} {path} {source}".replace("\\", "/").lower()


def _looks_like_qc_json_artifact(path: Path, source: str) -> bool:
    text = _artifact_text_for_matching(path, source)
    return any(
        token in text
        for token in ("qc", "quality", "motion", "mean_fd", "fd_", "metrics")
    )


def _json_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str | int | float | bool):
        text = str(value).strip()
        return text if text else None
    return None


def _first_scalar_field(value: Any, keys: tuple[str, ...], depth: int = 0) -> str | None:
    if depth > 3:
        return None
    if isinstance(value, dict):
        for key in keys:
            scalar = _json_scalar(value.get(key))
            if scalar:
                return scalar
        for item in value.values():
            found = _first_scalar_field(item, keys, depth + 1)
            if found:
                return found
    elif isinstance(value, list):
        for item in value[:10]:
            found = _first_scalar_field(item, keys, depth + 1)
            if found:
                return found
    return None


def _json_message_sample(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _message_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    messages: list[str] = []
    for item in items[:10]:
        if isinstance(item, str):
            if item:
                messages.append(item)
        elif isinstance(item, int | float | bool):
            messages.append(str(item))
        elif isinstance(item, dict):
            message = _first_scalar_field(
                item,
                ("message", "error_message", "error", "reason", "detail"),
            )
            if message:
                messages.append(message)
            else:
                messages.append(_json_message_sample(item))
    return messages


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "pass", "passed", "success", "ok"}:
            return True
        if normalized in {"false", "no", "0", "fail", "failed", "error"}:
            return False
    return None


def _status_booleans(status: Any) -> tuple[bool | None, bool | None]:
    normalized = str(status or "").strip().upper()
    if normalized in {"PASS", "PASSED", "SUCCESS", "OK", "TRUE"}:
        return True, False
    if normalized in {"FAIL", "FAILED", "ERROR", "FALSE"}:
        return False, True
    return None, None


def _metric_rows_from_value(value: Any) -> list[dict[str, str]]:
    metrics: list[dict[str, str]] = []
    if isinstance(value, dict):
        iterable = value.items()
    elif isinstance(value, list):
        iterable = []
        for item in value:
            if isinstance(item, dict):
                label = _first_scalar_field(item, ("label", "name", "key", "metric"))
                metric_value = _first_scalar_field(item, ("value", "score", "result"))
                if label and metric_value:
                    metrics.append({"label": label, "value": metric_value})
                if len(metrics) >= ARTIFACT_QC_METRIC_LIMIT:
                    return metrics
        return metrics
    else:
        return metrics

    for key, item in iterable:
        scalar = _json_scalar(item)
        if scalar:
            metrics.append({"label": str(key), "value": scalar})
        if len(metrics) >= ARTIFACT_QC_METRIC_LIMIT:
            break
    return metrics


def _metric_rows_from_payload(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    metrics = _metric_rows_from_value(payload.get("metrics"))
    if metrics:
        return metrics
    metric_tokens = ("metric", "mean", "fd", "dvars", "snr", "tsnr", "motion")
    derived: dict[str, Any] = {}
    for key, value in payload.items():
        key_text = str(key).lower()
        if any(token in key_text for token in metric_tokens):
            derived[str(key)] = value
    return _metric_rows_from_value(derived)


def _qc_summary_from_json(payload: Any, status: Any = None) -> dict[str, Any]:
    passed_from_status, failed_from_status = _status_booleans(status)
    passed = passed_from_status
    failed = failed_from_status
    if isinstance(payload, dict):
        passed = (
            _bool_or_none(payload.get("passed"))
            if payload.get("passed") is not None
            else _bool_or_none(payload.get("ok"))
        )
        if passed is None:
            passed = passed_from_status
        failed = _bool_or_none(payload.get("failed"))
        if failed is None:
            failed = failed_from_status

    warnings = _message_list(payload.get("warnings")) if isinstance(payload, dict) else []
    errors = _message_list(payload.get("errors")) if isinstance(payload, dict) else []
    error_message = (
        _first_scalar_field(payload, ("error_message", "error", "message"))
        or (errors[0] if errors else None)
    )
    return {
        "status": status,
        "passed": passed,
        "failed": failed,
        "warnings": warnings[:5],
        "metrics": _metric_rows_from_payload(payload),
        "subject_id": _first_scalar_field(payload, ("subject_id", "subject")),
        "node_id": _first_scalar_field(payload, ("node_id", "node")),
        "error_message": error_message,
    }


def _enrich_qc_json_artifact(
    artifact: dict[str, Any],
    path: Path,
    source: str,
    warnings: list[str],
) -> None:
    if artifact["kind"] != "json" or not artifact["exists"] or not _looks_like_qc_json_artifact(path, source):
        return
    size_bytes = artifact.get("size_bytes")
    if not isinstance(size_bytes, int) or size_bytes > ARTIFACT_PREVIEW_MAX_BYTES:
        artifact["qc_summary"] = {"truncated": True}
        return
    try:
        payload = read_json_artifact(path)
    except JSONDecodeError as exc:
        warnings.append(f"ARTIFACT_JSON_INVALID: {path}: {exc.msg}")
        return
    except Exception as exc:
        warnings.append(f"ARTIFACT_QC_SUMMARY_READ_FAILED: {path}: {exc}")
        return
    json_summary = json_preview_summary(payload)
    artifact["json_summary"] = json_summary
    qc_summary = _qc_summary_from_json(payload, json_summary.get("status"))
    qc_summary["json_summary"] = json_summary
    artifact["qc_summary"] = qc_summary


def _error_excerpt_for_artifact(path: Path, kind: str) -> str | None:
    if kind not in {"log", "text"} or not path.exists() or not path.is_file():
        return None
    with path.open("rb") as handle:
        raw = handle.read(ARTIFACT_PREVIEW_MAX_BYTES + 1)
    truncated = len(raw) > ARTIFACT_PREVIEW_MAX_BYTES
    raw = raw[:ARTIFACT_PREVIEW_MAX_BYTES]
    text = raw.decode("utf-8", errors="replace")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    interesting_index = next(
        (
            index
            for index, line in enumerate(lines)
            if any(token in line.lower() for token in ("error", "failed", "traceback", "stderr"))
        ),
        0,
    )
    excerpt = "\n".join(lines[interesting_index : interesting_index + 8])
    if truncated or len(excerpt) > ARTIFACT_ERROR_EXCERPT_MAX_CHARS:
        excerpt = excerpt[:ARTIFACT_ERROR_EXCERPT_MAX_CHARS].rstrip() + "..."
    return excerpt


def _artifact_record(
    path: Path,
    project: ProjectDetail,
    record: RunLinkRecord,
    source: str,
) -> dict[str, Any]:
    roots = _project_artifact_roots(project, record)
    exists = path.exists() and path.is_file()
    warnings: list[str] = []
    if not exists:
        warnings.append(f"ARTIFACT_FILE_MISSING: {path}")
    suffix = _path_suffix(path)
    artifact = {
        "artifact_id": artifact_id_for_path(path),
        "name": path.name,
        "kind": _artifact_kind(path),
        "path": str(path),
        "relative_path": _relative_artifact_path(path, project, roots),
        "exists": exists,
        "size_bytes": int(path.stat().st_size) if exists else None,
        "modified_at": _modified_at(path),
        "previewable": exists and _is_previewable(path),
        "warnings": warnings,
        "source": source,
        "suffix": suffix,
    }
    node_id = _node_id_from_source(source, path)
    if node_id:
        artifact["node_id"] = node_id
    if "audit_records" in {part.casefold() for part in path.parts}:
        artifact["artifact_type"] = "audit_record"
        artifact["registration_status"] = "persisted"
    if source.endswith("node_states") or "/states/" in str(path).replace("\\", "/"):
        artifact["artifact_type"] = "node_state"
    _enrich_qc_json_artifact(artifact, path, source, warnings)
    error_excerpt = _error_excerpt_for_artifact(path, str(artifact["kind"]))
    if error_excerpt:
        artifact["error_excerpt"] = error_excerpt
    return artifact


def _registry_paths(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> list[Path]:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    project_dir = metadata.get("project_dir")
    if not project_dir and record.project_config_path:
        project_dir = str(Path(record.project_config_path).expanduser().resolve().parent)
    if not project_dir:
        return []
    root = Path(str(project_dir)).expanduser().resolve()
    return _dedupe_paths(
        [
            root / "work" / "pipeline_runs" / record.run_id / ARTIFACT_REGISTRY_FILENAME,
            root / "preprocessing_runs" / record.run_id / ARTIFACT_REGISTRY_FILENAME,
        ]
    )


def _scoped_registry_candidates(
    project: ProjectDetail,
    record: RunLinkRecord,
    *,
    base_dirs: list[Path],
) -> tuple[list[tuple[Path, dict[str, Any]]], list[str]]:
    candidates: list[tuple[Path, dict[str, Any]]] = []
    warnings: list[str] = []
    for registry_path in _registry_paths(project, record):
        if not registry_path.is_file():
            continue
        try:
            payload = read_json_artifact(registry_path)
        except Exception as exc:
            warnings.append(f"ARTIFACT_REGISTRY_READ_FAILED: {registry_path}: {exc}")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"ARTIFACT_REGISTRY_INVALID: {registry_path}")
            continue
        registry_run_id = str(payload.get("preprocessing_run_id") or "")
        if registry_run_id and registry_run_id != record.run_id:
            warnings.append(
                f"ARTIFACT_REGISTRY_RUN_MISMATCH: expected {record.run_id}, got {registry_run_id}"
            )
            continue
        for item in payload.get("artifacts", []):
            if not isinstance(item, dict) or not item.get("path"):
                continue
            item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            item_run_id = str(
                item_metadata.get("preprocessing_run_id")
                or item_metadata.get("pipeline_run_id")
                or ""
            )
            if item_run_id and item_run_id != record.run_id:
                warnings.append(
                    "ARTIFACT_REGISTRY_ENTRY_RUN_MISMATCH: "
                    f"expected {record.run_id}, got {item_run_id}"
                )
                continue
            resolved, warning = _resolve_candidate_path(
                str(item["path"]),
                project,
                record,
                base_dirs=base_dirs,
            )
            if warning:
                warnings.append(warning)
                continue
            assert resolved is not None
            normalized_parts = [part.casefold() for part in resolved.parts]
            for run_root_name in ("preprocessing_runs", "preprocessing_native_runs"):
                if run_root_name not in normalized_parts:
                    continue
                run_index = normalized_parts.index(run_root_name) + 1
                if run_index < len(resolved.parts) and resolved.parts[run_index] != record.run_id:
                    warnings.append(
                        "ARTIFACT_REGISTRY_ENTRY_RUN_MISMATCH: "
                        f"selected {record.run_id}, path belongs to {resolved.parts[run_index]}"
                    )
                    resolved = None
                break
            if resolved is not None:
                candidates.append((resolved, item))
    return candidates, _dedupe(warnings)


def _read_node_state_candidates(
    path: Path,
    project: ProjectDetail,
    record: RunLinkRecord,
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    if not path.exists() or not path.is_file() or _path_suffix(path) != ".json":
        return [], warnings
    try:
        payload = read_json_artifact(path)
    except Exception as exc:
        warnings.append(f"NODE_STATE_READ_FAILED: {path}: {exc}")
        return [], warnings
    if not isinstance(payload, dict):
        return [], warnings
    candidates: list[str] = []
    for key in ("outputs", "stdout_log", "stderr_log", "log_path", "result_json"):
        candidates.extend(_collect_path_candidates(payload.get(key)))
    return candidates, warnings


def discover_run_artifacts(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    summary_raw, summary_warnings, summary_error = _load_summary_raw(project, record)
    warnings.extend(summary_warnings)
    if summary_error:
        warnings.append(summary_error)

    base_dirs: list[Path] = []
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    if metadata.get("project_dir"):
        base_dirs.append(Path(str(metadata["project_dir"])).expanduser().resolve())
    if record.summary_path:
        try:
            base_dirs.append(Path(record.summary_path).expanduser().resolve().parent)
        except Exception:
            pass
    if record.project_config_path:
        base_dirs.append(Path(record.project_config_path).expanduser().resolve().parent)
    base_dirs = _dedupe_paths(base_dirs or [Path.cwd().resolve()])

    raw_candidates: list[tuple[str, str]] = []
    if record.pipeline_path:
        raw_candidates.append((record.pipeline_path, "run_link.pipeline_path"))
    if record.summary_path:
        raw_candidates.append((record.summary_path, "run_link.summary_path"))
    if isinstance(record.payload, dict):
        payload_for_discovery = record.payload
        audit_payload = record.payload.get("audit")
        if isinstance(audit_payload, dict) and audit_payload.get("project_audit_path"):
            # The canonical audit path can point at the execution-audit store outside
            # the project.  Once a project-scoped projection exists, expose only that
            # projection in Runs instead of inventing a missing project-relative copy.
            payload_for_discovery = {
                **record.payload,
                "audit": {
                    key: value
                    for key, value in audit_payload.items()
                    if key != "audit_path"
                },
            }
        for candidate in _collect_path_candidates(payload_for_discovery):
            raw_candidates.append((candidate, "run_link.payload"))

    if summary_raw:
        for key in ("outputs", "artifacts", "reports", "report_paths", "node_states"):
            for candidate in _collect_path_candidates(summary_raw.get(key)):
                raw_candidates.append((candidate, f"summary.{key}"))
        for index, node in enumerate(_node_results(summary_raw)):
            for candidate in _collect_path_candidates(node):
                raw_candidates.append((candidate, f"summary.node_results[{index}]"))

    discovered_paths: dict[str, dict[str, Any]] = {}
    node_state_paths: list[Path] = []
    for raw_path, source in raw_candidates:
        resolved, warning = _resolve_candidate_path(
            raw_path,
            project,
            record,
            base_dirs=base_dirs,
        )
        if warning:
            warnings.append(warning)
            continue
        assert resolved is not None
        artifact = _artifact_record(resolved, project, record, source)
        discovered_paths.setdefault(str(resolved).casefold(), artifact)
        if source.endswith("node_states") or "/states/" in str(resolved).replace("\\", "/"):
            node_state_paths.append(resolved)

    for node_state_path in _dedupe_paths(node_state_paths):
        state_candidates, state_warnings = _read_node_state_candidates(
            node_state_path,
            project,
            record,
        )
        warnings.extend(state_warnings)
        state_base_dirs = _dedupe_paths([node_state_path.parent, *base_dirs])
        for raw_path in state_candidates:
            resolved, warning = _resolve_candidate_path(
                raw_path,
                project,
                record,
                base_dirs=state_base_dirs,
            )
            if warning:
                warnings.append(warning)
                continue
            assert resolved is not None
            artifact = _artifact_record(
                resolved,
                project,
                record,
                f"node_state:{node_state_path.name}",
            )
            discovered_paths.setdefault(str(resolved).casefold(), artifact)

    registry_candidates, registry_warnings = _scoped_registry_candidates(
        project,
        record,
        base_dirs=base_dirs,
    )
    warnings.extend(registry_warnings)
    for resolved, registry_item in registry_candidates:
        artifact = _artifact_record(
            resolved,
            project,
            record,
            f"artifact_registry:{record.run_id}",
        )
        artifact.update(
            {
                "registered_artifact_id": str(registry_item.get("artifact_id") or ""),
                "artifact_type": str(registry_item.get("artifact_type") or ""),
                "stage_id": str(registry_item.get("stage_id") or ""),
                "subject_id": str(registry_item.get("subject_id") or ""),
                "registration_status": "registered",
            }
        )
        discovered_paths.setdefault(str(resolved).casefold(), artifact)

    artifacts = sorted(
        discovered_paths.values(),
        key=lambda item: (
            not bool(item.get("exists")),
            str(item.get("kind") or ""),
            str(item.get("relative_path") or ""),
        ),
    )
    return artifacts, _dedupe(warnings)


def find_run_artifact(
    project: ProjectDetail,
    record: RunLinkRecord,
    artifact_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    artifacts, warnings = discover_run_artifacts(project, record)
    for artifact in artifacts:
        if artifact.get("artifact_id") == artifact_id:
            return artifact, warnings
    return None, warnings
