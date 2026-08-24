"""Pure run summary preview helpers."""

from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any

from src.backend.app.schemas.desktop import ProjectDetail, RunLinkRecord
from src.backend.app.tools.artifact_utils import read_json_artifact

RAW_SUMMARY_MAX_CHARS = 20_000
SUMMARY_WARNING_LIMIT = 50
OUTPUT_ITEM_LIMIT = 50


def _dedupe(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _relative_to_any(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False


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


def recovery_run_output_roots(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> list[Path]:
    """Return output roots only for a fully bound project-owned recovery run."""
    payload = record.payload if isinstance(record.payload, dict) else {}
    attempt_id = str(payload.get("recovery_attempt_id") or "")
    output_namespace = str(payload.get("output_namespace") or "")
    attempt_root_value = payload.get("attempt_output_root")
    state_root_value = payload.get("state_root")
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    project_root_value = metadata.get("project_dir")
    if not all(
        (
            attempt_id,
            attempt_root_value,
            state_root_value,
            project_root_value,
            record.project_config_path,
            record.pipeline_path,
        )
    ):
        return []
    attempt_root = Path(str(attempt_root_value)).expanduser().resolve()
    project_root = Path(str(project_root_value)).expanduser().resolve()
    rawdata_roots = _rawdata_roots(project)
    try:
        attempt_root.relative_to(project_root)
    except ValueError:
        return []
    if _relative_to_any(attempt_root, rawdata_roots):
        return []
    if (
        output_namespace != f"recovery_attempts/{attempt_id}"
        or attempt_root.name != attempt_id
        or attempt_root.parent.name != "recovery_attempts"
        or Path(record.project_config_path).expanduser().resolve()
        != attempt_root / "control" / "project_config.yaml"
        or Path(record.pipeline_path).expanduser().resolve()
        != attempt_root / "control" / "pipeline.yaml"
        or Path(str(state_root_value)).expanduser().resolve() != attempt_root / "work"
    ):
        return []
    return [
        attempt_root / "work",
        attempt_root / "reports",
        attempt_root / "logs",
        attempt_root / "derivatives",
    ]


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
                base / "reports",
                base / "logs",
                base / "derivatives",
            ]
        )
    roots.extend(recovery_run_output_roots(project, record))
    return _dedupe_paths(roots)


def _rawdata_roots(project: ProjectDetail) -> list[Path]:
    metadata = project.metadata if isinstance(project.metadata, dict) else {}
    rawdata_dir = metadata.get("rawdata_dir")
    if not rawdata_dir:
        return []
    return [Path(str(rawdata_dir)).expanduser().resolve()]


def resolve_run_summary_path(
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


def _node_status(node: dict[str, Any]) -> str:
    status = str(node.get("status") or "").upper()
    if status:
        return status
    if node.get("ok") is True:
        return "SUCCESS"
    if node.get("ok") is False:
        return "FAILED"
    return "UNKNOWN"


def _node_id(node: dict[str, Any], index: int) -> str:
    return str(
        node.get("node_id")
        or node.get("node")
        or node.get("id")
        or node.get("name")
        or f"node_{index + 1}"
    )


def _count_nodes(raw: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, int | None]:
    statuses = [_node_status(node) for node in nodes]
    total = (
        _int_or_none(raw.get("nodes_total"))
        or _int_or_none(raw.get("node_count"))
        or (len(nodes) if nodes else _int_or_none(raw.get("nodes_count")))
    )
    succeeded = (
        _int_or_none(raw.get("nodes_succeeded"))
        if raw.get("nodes_succeeded") is not None
        else _int_or_none(raw.get("nodes_success"))
    )
    failed = _int_or_none(raw.get("nodes_failed"))
    skipped = _int_or_none(raw.get("nodes_skipped"))

    if succeeded is None and nodes:
        succeeded = sum(1 for status in statuses if status in {"SUCCESS", "COMPLETED", "PASS", "PASSED"})
    if failed is None and nodes:
        failed = sum(1 for status in statuses if status in {"FAILED", "ERROR", "FAIL"})
    if skipped is None and nodes:
        skipped = sum(1 for status in statuses if status in {"SKIPPED", "CANCELLED"})

    return {
        "nodes_total": total,
        "nodes_succeeded": succeeded,
        "nodes_failed": failed,
        "nodes_skipped": skipped,
    }


def _summary_warnings(raw: dict[str, Any], nodes: list[dict[str, Any]]) -> list[str]:
    warnings = _string_list(raw.get("warnings"))
    for index, node in enumerate(nodes):
        node_label = _node_id(node, index)
        for warning in _string_list(node.get("warnings")):
            warnings.append(f"{node_label}: {warning}")
    return _dedupe(warnings)[:SUMMARY_WARNING_LIMIT]


def _summary_errors(raw: dict[str, Any], nodes: list[dict[str, Any]]) -> list[Any]:
    errors: list[Any] = list(raw.get("errors") or []) if isinstance(raw.get("errors"), list) else []
    for index, node in enumerate(nodes):
        if _node_status(node) not in {"FAILED", "ERROR", "FAIL"}:
            continue
        node_errors = node.get("errors")
        errors.append(
            {
                "node_id": _node_id(node, index),
                "status": _node_status(node),
                "errors": node_errors if isinstance(node_errors, list) else _string_list(node_errors),
            }
        )
    return errors[:SUMMARY_WARNING_LIMIT]


def _failed_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failed: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        if _node_status(node) not in {"FAILED", "ERROR", "FAIL"}:
            continue
        failed.append(
            {
                "node_id": _node_id(node, index),
                "status": _node_status(node),
                "errors": _string_list(node.get("errors")),
            }
        )
    return failed[:SUMMARY_WARNING_LIMIT]


def _summary_outputs(raw: dict[str, Any], nodes: list[dict[str, Any]]) -> dict[str, Any]:
    outputs = raw.get("outputs")
    if isinstance(outputs, dict):
        return dict(list(outputs.items())[:OUTPUT_ITEM_LIMIT])
    if isinstance(outputs, list):
        return {
            "items": outputs[:OUTPUT_ITEM_LIMIT],
            "truncated": len(outputs) > OUTPUT_ITEM_LIMIT,
        }

    node_outputs: dict[str, Any] = {}
    for index, node in enumerate(nodes):
        outputs_value = node.get("outputs")
        if outputs_value:
            node_outputs[_node_id(node, index)] = outputs_value
        if len(node_outputs) >= OUTPUT_ITEM_LIMIT:
            node_outputs["truncated"] = True
            break
    if node_outputs:
        return node_outputs

    node_states = raw.get("node_states")
    if isinstance(node_states, list):
        return {
            "node_states": node_states[:OUTPUT_ITEM_LIMIT],
            "truncated": len(node_states) > OUTPUT_ITEM_LIMIT,
        }
    return {}


def _raw_preview(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    encoded = json.dumps(raw, ensure_ascii=False)
    if len(encoded) <= RAW_SUMMARY_MAX_CHARS:
        return raw, False
    return (
        {
            "truncated": True,
            "size_chars": len(encoded),
            "top_level_keys": list(raw.keys())[:OUTPUT_ITEM_LIMIT],
            "note": "Raw summary exceeded preview budget and was truncated.",
        },
        True,
    )


def summary_preview_payload(raw: dict[str, Any], record: RunLinkRecord) -> dict[str, Any]:
    nodes = _node_results(raw)
    counts = _count_nodes(raw, nodes)
    raw_payload, raw_truncated = _raw_preview(raw)
    preview = {
        "run_id": raw.get("run_id") or record.run_id,
        "status": raw.get("status") or raw.get("pipeline_status") or record.status,
        "started_at": raw.get("started_at") or raw.get("start_time"),
        "finished_at": raw.get("finished_at") or raw.get("ended_at") or raw.get("end_time"),
        **counts,
        "warnings": _summary_warnings(raw, nodes),
        "outputs": _summary_outputs(raw, nodes),
        "errors": _summary_errors(raw, nodes),
        "failed_nodes": _failed_nodes(nodes),
        "raw": raw_payload,
        "raw_truncated": raw_truncated,
    }
    return preview


def load_run_summary_preview(
    project: ProjectDetail,
    record: RunLinkRecord,
) -> tuple[dict[str, Any] | None, list[str], str | None]:
    target, warnings = resolve_run_summary_path(project, record)
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
    return summary_preview_payload(raw, record), [], None
