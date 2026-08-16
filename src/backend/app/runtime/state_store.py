from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.core.exceptions import StateStoreError
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.execution_state import PersistedNodeState

STATE_SCHEMA_VERSION = "state-store-v2"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def determine_status_from_result(result: dict[str, Any]) -> str:
    return "SUCCESS" if result.get("ok") else "FAILED"


def _write_state_json(path: Path, data: dict[str, Any]) -> Path:
    try:
        return atomic_write_json(path, data, schema_version=STATE_SCHEMA_VERSION)
    except Exception as exc:
        raise StateStoreError(
            "Failed to write runtime state file.",
            details={"path": str(path)},
        ) from exc


def write_node_state(
    run_id: str,
    node_id: str,
    subject: str,
    status: str,
    started_at: str,
    ended_at: str | None,
    result: dict[str, Any],
    work_dir: str,
) -> Path:
    # For subject-level nodes, store in subject subdirectory
    if subject != "project":
        state_dir = Path(work_dir) / "states" / run_id / subject
    else:
        state_dir = Path(work_dir) / "states" / run_id
    state_dir.mkdir(parents=True, exist_ok=True)

    state_path = state_dir / f"{node_id}.json"
    previous: dict[str, Any] = {}
    if state_path.exists():
        try:
            import json

            raw = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and raw.get("_schema_version") == STATE_SCHEMA_VERSION:
                previous = raw
        except (OSError, ValueError):
            # The atomic replacement below is the authoritative result.  A
            # malformed prior record must not turn a terminal update into an
            # unrecorded execution outcome.
            previous = {}

    effective_started_at = str(previous.get("started_at") or started_at)
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "run_id": run_id,
        "subject": subject,
        "node": node_id,
        "status": status,
        "started_at": effective_started_at,
        "ended_at": ended_at,
        "updated_at": now_iso(),
        "log_path": result.get("stdout_log"),
        "stderr_log": result.get("stderr_log"),
        "outputs": result.get("outputs", result.get("expected_outputs", [])),
        "metrics": result.get("metrics", {}),
        "warnings": result.get("warnings", []),
        "errors": result.get("errors", []),
        "result_json": result.get("result_json"),
        "returncode": result.get("returncode"),
    }
    state = PersistedNodeState.model_validate(payload).model_dump()
    state.pop("schema_version")
    return _write_state_json(state_path, state)


def write_pipeline_summary(
    run_id: str,
    pipeline_id: str,
    status: str,
    started_at: str,
    ended_at: str,
    node_states: list[str],
    node_results: list[dict[str, Any]],
    errors: list[str],
    work_dir: str,
    scheduler: dict[str, Any] | None = None,
    duration_seconds: float = 0.0,
) -> Path:
    summary_dir = Path(work_dir) / "pipeline_runs" / run_id
    summary_dir.mkdir(parents=True, exist_ok=True)

    nodes_total = len(node_results)
    nodes_success = sum(1 for r in node_results if r.get("ok"))
    nodes_failed = nodes_total - nodes_success

    summary: dict[str, Any] = {
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "status": status,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "nodes_total": nodes_total,
        "nodes_success": nodes_success,
        "nodes_failed": nodes_failed,
        "nodes_skipped": 0,
        "node_states": node_states,
        "errors": errors,
    }

    if scheduler:
        summary["scheduler"] = scheduler

    summary_path = summary_dir / "summary.json"
    return _write_state_json(summary_path, summary)
