"""Build immutable, small, redacted snapshots for the Agent Harness."""

from __future__ import annotations

import json
import re
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import AgentHarnessContext


class HarnessContextBuilder:
    """The sole context builder; it never reads raw files or full transcripts."""

    MAX_BYTES = 32 * 1024
    _SECRET_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|authorization)", re.I)
    _UNSAFE_KEY = re.compile(r"(?:rawdata|image|dicom|nifti|transcript|prompt|log)", re.I)

    def build(self, *, lifecycle, project, evidence_snapshot=None) -> AgentHarnessContext:
        metadata = project.metadata if project is not None and isinstance(project.metadata, dict) else {}
        command_context = lifecycle.command_context if isinstance(lifecycle.command_context, dict) else {}
        memory = command_context.get("memory_context")
        safe_memory = self._memory_fields(memory)
        fields: dict[str, Any] = {
            "goal": self._short_text(lifecycle.goal_text),
            "lifecycle_state": lifecycle.state,
            "confirmed_answers": self._safe_object(command_context.get("science_answers"), max_items=12),
            "project_evidence": self._safe_snapshot(evidence_snapshot) if evidence_snapshot is not None else self._safe_project_evidence(metadata),
            "reviewed_plan": {
                "reviewed_plan_id": lifecycle.reviewed_plan_id,
                "execution_ticket_id": lifecycle.execution_ticket_id,
                "run_id": lifecycle.run_id,
            },
            "memory": safe_memory,
        }
        omitted: list[str] = []
        fields, omitted = self._truncate(fields, omitted)
        project_snapshot_hash = stable_hash(fields.get("project_evidence") or {})
        memory_hash = stable_hash(safe_memory) if safe_memory else None
        context_hash = stable_hash(
            {
                "lifecycle_id": lifecycle.lifecycle_id,
                "project_id": lifecycle.project_id,
                "fields": fields,
                "omitted_fields": omitted,
                "prompt_template_version": 1,
            }
        )
        return AgentHarnessContext(
            context_hash=context_hash,
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            allowed_fields_json=fields,
            memory_context_hash=memory_hash,
            project_snapshot_hash=project_snapshot_hash,
            omitted_fields=tuple(omitted),
        )

    def _truncate(self, fields: dict[str, Any], omitted: list[str]) -> tuple[dict[str, Any], list[str]]:
        """Apply the published deterministic omission order."""
        candidate = dict(fields)
        for key in ("old_trace", "project_evidence", "explanations", "memory"):
            if self._size(candidate) <= self.MAX_BYTES:
                break
            if key == "project_evidence":
                evidence = candidate.get(key)
                if isinstance(evidence, dict):
                    candidate[key] = {name: value for name, value in evidence.items() if name in {"data_state", "subject_count"}}
                    omitted.append("project_evidence_detail")
            elif key in candidate:
                candidate.pop(key, None)
                omitted.append(key)
        if self._size(candidate) > self.MAX_BYTES:
            # Goal/state remain available even if an unexpected metadata object is huge.
            candidate = {"goal": candidate.get("goal", ""), "lifecycle_state": candidate.get("lifecycle_state", "")}
            omitted.append("nonessential_context")
        return candidate, omitted

    @staticmethod
    def _size(value: object) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def _safe_project_evidence(self, metadata: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "data_state", "subject_count", "dataset_type", "registered_artifact_count",
            "agent_science_decisions", "conversion_status", "preprocessing_status",
        }
        return self._safe_object({key: metadata.get(key) for key in allowed if key in metadata}, max_items=24)

    def _safe_snapshot(self, snapshot) -> dict[str, Any]:
        """Expose only bounded structured facts and stable source IDs to the model."""
        return self._safe_object(
            {
                "snapshot_hash": snapshot.snapshot_hash,
                "facts": [fact.model_dump(mode="json") for fact in snapshot.facts],
                "missing": list(snapshot.missing),
                "warnings": [warning.model_dump(mode="json") for warning in snapshot.warnings],
            },
            max_items=24,
        )

    def _memory_fields(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {"context_hash", "memory_ids", "planner_constraints", "decision_suggestions"}
        return self._safe_object({key: value.get(key) for key in allowed if key in value}, max_items=24)

    def _safe_object(self, value: object, *, max_items: int) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key in sorted(value)[:max_items]:
                key_text = str(key)
                if self._SECRET_KEY.search(key_text) or self._UNSAFE_KEY.search(key_text):
                    continue
                result[key_text] = self._safe_object(value[key], max_items=max_items)
            return result
        if isinstance(value, (list, tuple)):
            return [self._safe_object(item, max_items=max_items) for item in value[:max_items]]
        if isinstance(value, str):
            return self._short_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self._short_text(str(value))

    @staticmethod
    def _short_text(value: object, limit: int = 1024) -> str:
        return str(value or "").replace("\x00", "")[:limit]
