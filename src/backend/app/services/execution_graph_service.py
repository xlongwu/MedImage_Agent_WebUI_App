"""Build safe, read-only execution graph projections."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.api.dependencies import ProjectStore
from src.backend.app.config import ProjectSettings
from src.backend.app.core.exceptions import NotFoundError, PipelineError, SafetyError
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.schemas.execution_graph import (
    ExecutionGraphEdge,
    ExecutionGraphNode,
    ExecutionGraphResponse,
    ExecutionGraphSubjectSummary,
)
from src.backend.app.schemas.execution_state import is_node_terminal, is_run_terminal
from src.backend.app.services.run_state_timeline import normalize_node_state, normalize_run_state
from src.backend.app.services.run_summary_preview import load_run_summary_preview


class ExecutionGraphService:
    def __init__(self, store: ProjectStore):
        self.store = store

    def build_preview_graph(self, *, project_id: str, plan: dict[str, object]) -> ExecutionGraphResponse:
        self._project(project_id)
        return self._build(project_id=project_id, reviewed_plan_id="preview", plan_hash=self._hash(plan), plan=plan)

    def build_plan_graph(self, *, project_id: str, reviewed_plan_id: str) -> ExecutionGraphResponse:
        record = self._reviewed_plan(project_id, reviewed_plan_id)
        return self._build(
            project_id=project_id,
            reviewed_plan_id=record.reviewed_plan_id,
            plan_hash=record.plan_hash,
            plan=record.payload.get("plan", record.payload),
        )

    def build_run_graph(self, *, project_id: str, run_id: str) -> ExecutionGraphResponse:
        project = self._project(project_id)
        link = self.store.get_run_link_by_run_id(project_id, run_id)
        if link is None:
            raise NotFoundError("Run not found", code="EXECUTION_GRAPH_RUN_NOT_FOUND")
        record = self._reviewed_plan(project_id, link.reviewed_plan_id)
        if link.project_id != project_id or record.plan_hash != link.payload.get("plan_hash", record.plan_hash):
            raise PipelineError(
                "Run link does not match its reviewed plan.",
                code="EXECUTION_GRAPH_PLAN_BINDING_MISMATCH",
                status_code=409,
            )
        graph = self._build(
            project_id=project_id,
            reviewed_plan_id=record.reviewed_plan_id,
            plan_hash=record.plan_hash,
            plan=record.payload.get("plan", record.payload),
            run_id=run_id,
            run_link=link,
            project=project,
            subject_total=self._subject_total(record),
        )
        return graph

    def _build(
        self,
        *,
        project_id: str,
        reviewed_plan_id: str,
        plan_hash: str,
        plan: object,
        run_id: str | None = None,
        run_link: Any | None = None,
        project: Any | None = None,
        subject_total: int | None = None,
    ) -> ExecutionGraphResponse:
        if not isinstance(plan, dict):
            raise PipelineError("Plan payload is invalid.", code="EXECUTION_GRAPH_INVALID_PLAN")
        validation = validate_plan(plan)
        if not validation.ok:
            raise PipelineError(
                "Plan cannot be projected as a graph.",
                code="EXECUTION_GRAPH_INVALID_PLAN",
                details={"issues": [issue.code for issue in validation.errors]},
            )
        raw_nodes = [item for item in plan.get("nodes", []) if isinstance(item, dict)]
        risk = self._risks(validation)
        states: dict[str, list[dict[str, Any]]] = defaultdict(list)
        summary_by_node: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        errors: list[str] = []
        run_state: str | None = None
        run_terminal = False
        if run_id and run_link and project:
            states, read_warnings, read_errors = self._read_runtime_states(project, run_link, run_id)
            warnings.extend(read_warnings)
            errors.extend(read_errors)
            summary_by_node, summary_warnings, summary_status = self._summary(project, run_link)
            warnings.extend(summary_warnings)
            run_state = normalize_run_state(summary_status or run_link.status)
            run_terminal = is_run_terminal(run_state)

        nodes: list[ExecutionGraphNode] = []
        for raw in raw_nodes:
            node_id = str(raw["id"])
            runtime_entries = states.get(node_id, [])
            merged = self._merge_node_state(
                runtime_entries,
                summary_by_node.get(node_id),
                subject_total=subject_total,
            )
            state = merged["state"] if run_id else "pending"
            nodes.append(ExecutionGraphNode(
                node_id=node_id,
                label=str(raw.get("name") or raw.get("label") or node_id),
                backend_id=str(raw.get("backend") or raw.get("runner") or raw.get("type") or "unknown"),
                parallel_level=str(raw.get("parallel_level") or "project"),
                depends_on=tuple(str(value) for value in raw.get("depends_on", []) if isinstance(value, str)),
                risk=risk.get(node_id, "normal"),
                planned_input_count=self._count(raw.get("inputs")),
                planned_output_count=self._count(raw.get("outputs")),
                parameter_keys=tuple(sorted(str(key) for key in (raw.get("params") or raw.get("parameters") or {}) if isinstance(key, str))),
                state=state,
                state_source=merged["source"] if run_id else "plan",
                started_at=merged["started_at"], ended_at=merged["ended_at"],
                duration_seconds=merged["duration_seconds"], subject_summary=merged["subject_summary"],
                warning_count=merged["warning_count"], error_count=merged["error_count"],
                actual_output_count=merged["actual_output_count"], current=state == "running",
            ))
        node_by_id = {node.node_id: node for node in nodes}
        edges: list[ExecutionGraphEdge] = []
        for node in nodes:
            for dep in node.depends_on:
                source = node_by_id.get(dep)
                if source is None:
                    errors.append(f"EXECUTION_GRAPH_DANGLING_EDGE:{dep}->{node.node_id}")
                    continue
                edges.append(ExecutionGraphEdge(
                    edge_id=f"{dep}->{node.node_id}", source_node_id=dep, target_node_id=node.node_id,
                    state=self._edge_state(source.state, node.state),
                ))
        if run_terminal and any(node.state == "running" for node in nodes):
            warnings.append("EXECUTION_GRAPH_STALE_RUNNING_NODE")
        graph_status = "partial" if errors or (run_terminal and any(node.state == "running" for node in nodes)) else "available"
        current_ids = tuple(node.node_id for node in nodes if node.current)
        ready_ids = tuple(node.node_id for node in nodes if node.state == "ready")
        terminal = sum(1 for node in nodes if is_node_terminal(node.state))
        structure = {"nodes": [(node.node_id, node.depends_on) for node in nodes], "plan_hash": plan_hash}
        state_payload = {"run_id": run_id, "run_state": run_state, "nodes": [(node.node_id, node.state, node.warning_count, node.error_count) for node in nodes]}
        return ExecutionGraphResponse(
            project_id=project_id, reviewed_plan_id=reviewed_plan_id, plan_hash=plan_hash,
            run_id=run_id, run_state=run_state, run_terminal=run_terminal, graph_status=graph_status,
            structure_hash=self._hash(structure), state_hash=self._hash(state_payload), generated_at=datetime.now(UTC),
            nodes=tuple(nodes), edges=tuple(edges), current_node_ids=current_ids, ready_node_ids=ready_ids,
            terminal_nodes=terminal, total_nodes=len(nodes),
            node_completion_percent=(round(terminal * 100 / len(nodes)) if run_id and nodes else None),
            warnings=tuple(dict.fromkeys(warnings)), errors=tuple(dict.fromkeys(errors)),
        )

    def _read_runtime_states(self, project: Any, link: Any, run_id: str) -> tuple[dict[str, list[dict[str, Any]]], list[str], list[str]]:
        if not run_id or any(part in {"", ".", ".."} for part in Path(run_id).parts) or Path(run_id).is_absolute():
            raise SafetyError("Unsafe run id.", code="EXECUTION_GRAPH_UNSAFE_RUN_ID")
        root = self._state_root(project, link)
        state_dir = (root / "states" / run_id).resolve()
        try:
            state_dir.relative_to(root)
        except ValueError as exc:
            raise SafetyError("State path escapes approved work directory.", code="EXECUTION_GRAPH_STATE_PATH_UNSAFE") from exc
        result: dict[str, list[dict[str, Any]]] = defaultdict(list)
        warnings: list[str] = []
        if not state_dir.exists():
            return result, warnings, []
        for path in state_dir.rglob("*.json"):
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("root is not object")
                if raw.get("_schema_version") != "state-store-v2":
                    warnings.append("EXECUTION_GRAPH_UNSUPPORTED_STATE_SCHEMA")
                    continue
                if raw.get("run_id") != run_id or not isinstance(raw.get("node"), str):
                    warnings.append("EXECUTION_GRAPH_STATE_ID_MISMATCH")
                    continue
                result[str(raw["node"])].append(raw)
            except (OSError, ValueError, json.JSONDecodeError):
                warnings.append("EXECUTION_GRAPH_STATE_FILE_INVALID")
        return result, warnings, []

    def _state_root(self, project: Any, link: Any) -> Path:
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        project_dir = Path(str(metadata.get("project_dir") or Path(link.project_config_path).parent)).expanduser().resolve()
        rawdata = metadata.get("rawdata_dir")
        settings = ProjectSettings.from_yaml(link.project_config_path)
        work = Path(settings.runtime.work_dir).expanduser()
        work_dir = work.resolve() if work.is_absolute() else (Path(link.project_config_path).parent / work).resolve()
        try:
            work_dir.relative_to(project_dir)
            if rawdata and work_dir.is_relative_to(Path(str(rawdata)).expanduser().resolve()):
                raise ValueError("work in rawdata")
        except ValueError as exc:
            raise SafetyError("Work directory is outside the approved project boundary.", code="EXECUTION_GRAPH_WORK_DIR_UNSAFE") from exc
        return work_dir

    def _summary(self, project: Any, link: Any) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
        preview, warnings, _ = load_run_summary_preview(project, link)
        if not preview:
            return {}, warnings, None
        raw = preview.get("raw") if isinstance(preview.get("raw"), dict) else {}
        items = raw.get("node_results", raw.get("nodes", [])) if isinstance(raw, dict) else []
        by_node = {str(item.get("node_id") or item.get("node")): item for item in items if isinstance(item, dict) and (item.get("node_id") or item.get("node"))}
        return by_node, warnings, str(preview.get("status") or "")

    def _merge_node_state(
        self,
        entries: list[dict[str, Any]],
        summary: dict[str, Any] | None,
        *,
        subject_total: int | None,
    ) -> dict[str, Any]:
        if entries:
            normalized = [normalize_node_state(str(item.get("status"))) for item in entries]
            counts = Counter(normalized)
            if counts["running"]:
                state = "running"
            elif counts["succeeded"] + counts["reused"] == len(normalized):
                state = "succeeded" if counts["succeeded"] else "reused"
            elif len({value for value in normalized if value in {"succeeded", "failed", "blocked", "timeout", "cancelled", "skipped", "reused"}}) > 1:
                state = "partial"
            elif counts["failed"] == len(normalized): state = "failed"
            elif counts["blocked"] == len(normalized): state = "blocked"
            elif counts["skipped"] == len(normalized): state = "skipped"
            elif counts["cancelled"] == len(normalized): state = "cancelled"
            elif counts["timeout"] == len(normalized): state = "timeout"
            else: state = normalized[0] if normalized else "unknown"
            summary_counts = Counter(normalized)
            subject = ExecutionGraphSubjectSummary(
                total=subject_total,
                observed=len(entries),
                **{
                    key: summary_counts.get(key, 0)
                    for key in ExecutionGraphSubjectSummary.model_fields
                    if key not in {"total", "observed"}
                },
            ) if any(item.get("subject") != "project" for item in entries) else None
            starts = [item.get("started_at") for item in entries if item.get("started_at")]
            ends = [item.get("ended_at") for item in entries if item.get("ended_at")]
            started = min(starts) if starts else None
            ended = max(ends) if ends else None
            duration = self._duration(started, ended)
            return {"state": state, "source": "runtime", "started_at": started, "ended_at": ended, "duration_seconds": duration, "subject_summary": subject, "warning_count": sum(len(item.get("warnings") or []) for item in entries), "error_count": sum(len(item.get("errors") or []) for item in entries), "actual_output_count": sum(self._count(item.get("outputs")) for item in entries)}
        if summary:
            state = normalize_node_state(str(summary.get("status") or ("SUCCESS" if summary.get("ok") else "FAILED")))
            return {"state": state, "source": "summary", "started_at": None, "ended_at": None, "duration_seconds": None, "subject_summary": None, "warning_count": self._count(summary.get("warnings")), "error_count": self._count(summary.get("errors")), "actual_output_count": self._count(summary.get("outputs"))}
        return {"state": "pending", "source": "plan", "started_at": None, "ended_at": None, "duration_seconds": None, "subject_summary": None, "warning_count": 0, "error_count": 0, "actual_output_count": 0}

    @staticmethod
    def _subject_total(record: Any) -> int | None:
        """Read only the registered dataset index; absent or malformed means unknown."""
        raw_path = getattr(record, "dataset_index_path", None)
        if not raw_path:
            return None
        try:
            payload = json.loads(Path(str(raw_path)).read_text(encoding="utf-8"))
            subjects = payload.get("subjects", []) if isinstance(payload, dict) else []
            return len(subjects) if isinstance(subjects, list) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    @staticmethod
    def _duration(started: str | None, ended: str | None) -> float | None:
        try:
            return max(0.0, (datetime.fromisoformat(str(ended)) - datetime.fromisoformat(str(started))).total_seconds())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _edge_state(source: str, target: str) -> str:
        if target == "running" and source in {"succeeded", "reused"}: return "active"
        if source in {"succeeded", "reused"} and target not in {"pending", "preflight", "ready"}: return "completed"
        if target == "blocked" and source in {"failed", "blocked", "timeout", "cancelled"}: return "blocked"
        return "pending"

    def _project(self, project_id: str) -> Any:
        project = self.store.get_project(project_id)
        if project is None: raise NotFoundError("Project not found", code="EXECUTION_GRAPH_PROJECT_NOT_FOUND")
        return project

    def _reviewed_plan(self, project_id: str, reviewed_plan_id: str) -> Any:
        record = self.store.get_reviewed_plan(reviewed_plan_id)
        if record is None or record.project_id != project_id: raise NotFoundError("Reviewed plan not found", code="EXECUTION_GRAPH_PLAN_NOT_FOUND")
        return record

    @staticmethod
    def _count(value: object) -> int:
        return len(value) if isinstance(value, (list, tuple, dict)) else 0

    @staticmethod
    def _hash(value: object) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")).hexdigest()

    @staticmethod
    def _risks(validation: Any) -> dict[str, str]:
        result = {node: "high" for node in validation.high_risk_nodes}
        result.update({node: "approval" for node in validation.approval_required_nodes if node not in result})
        result.update({node: "unknown" for node in validation.unknown_nodes if node not in result})
        return result
