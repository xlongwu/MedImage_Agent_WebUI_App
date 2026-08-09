"""Build immutable, small, redacted Context v2 snapshots for the Harness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import (
    AgentHarnessContext,
    AgentHarnessContextSection,
    AgentHarnessContextSections,
)


class AgentContextLimitExceededError(ValueError):
    """Raised before a provider call when required safe context cannot fit."""

    code = "AGENT_CONTEXT_LIMIT_EXCEEDED"


@dataclass(frozen=True)
class HarnessContextSources:
    """Explicit, already-read inputs to the sole Context v2 builder.

    The builder deliberately performs no store calls.  This keeps its input
    surface auditable and lets tests vary every dynamic source independently.
    """

    lifecycle: object
    project: object | None
    evidence_snapshot: object | None = None
    reviewed_plan: object | None = None
    run_link: object | None = None
    observation: object | None = None
    evaluation: object | None = None
    recovery_proposal: object | None = None
    result_summary: object | None = None
    last_step: object | None = None
    attempt: object | None = None
    prompt_template_version: str = "agent-harness-prompt-v2"
    skill_refs: tuple[SkillContextRef, ...] = ()
    skill_error_codes: tuple[str, ...] = ()
    policy_version: str = "agent-harness-policy-v2"
    redaction_policy_version: str = "agent-harness-redaction-v2"


class HarnessContextBuilder:
    """The sole context builder; it never reads raw files or full transcripts."""

    MAX_BYTES = 32 * 1024
    _SECRET_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|authorization)", re.I)
    _UNSAFE_KEY = re.compile(r"(?:rawdata|image|dicom|nifti|transcript|prompt|log)", re.I)
    _ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![:/])\/)[^\s,;\]\)}]+")

    SECTION_ORDER = (
        "goal", "policy", "project_evidence", "decision_state", "plan_state",
        "execution_state", "latest_observation", "last_action_result", "memory_context", "budget",
    )
    REQUIRED_SECTIONS = ("goal", "policy", "decision_state", "last_action_result", "budget")

    def build(self, *, sources: HarnessContextSources) -> AgentHarnessContext:
        lifecycle = sources.lifecycle
        metadata = (
            sources.project.metadata
            if sources.project is not None and isinstance(sources.project.metadata, dict)
            else {}
        )
        command_context = lifecycle.command_context if isinstance(lifecycle.command_context, dict) else {}
        memory = command_context.get("memory_context")
        safe_memory = self._memory_fields(memory)
        section_inputs: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
            "goal": ({
                "goal": self._short_text(lifecycle.goal_text, limit=512),
                "lifecycle_state": lifecycle.state,
                "goal_contract_hash": self._safe_ref(command_context.get("goal_contract_hash")),
                "revision": self._safe_scalar(command_context.get("goal_revision")),
            }, (f"lifecycle:{lifecycle.lifecycle_id}",)),
            "policy": ({
                "action_allowlist": ["read_evidence", "request_decision", "draft_plan", "explain_result", "propose_recovery", "finish"],
                "read_only_boundary": True,
                "approval_or_execution_actions": False,
            }, ("policy:agent-harness-v2",)),
            "project_evidence": (
                self._safe_snapshot(sources.evidence_snapshot)
                if sources.evidence_snapshot is not None else self._safe_project_evidence(metadata),
                self._snapshot_refs(sources.evidence_snapshot, lifecycle.project_id),
            ),
            "decision_state": ({
                "confirmed_answers": self._safe_object(command_context.get("science_answers"), max_items=12),
                "pending_batch_id": self._safe_ref(getattr(getattr(lifecycle, "pending_decision_batch", None), "batch_id", None)),
                "pending_item_count": len(getattr(getattr(lifecycle, "pending_decision_batch", None), "items", ()) or ()),
            }, (f"lifecycle:{lifecycle.lifecycle_id}",)),
            "plan_state": (self._plan_state(lifecycle, sources.reviewed_plan), self._plan_refs(lifecycle, sources.reviewed_plan)),
            "execution_state": (self._execution_state(lifecycle, sources.run_link), self._execution_refs(lifecycle, sources.run_link)),
            "latest_observation": (self._observation_state(sources.observation, sources.evaluation, sources.recovery_proposal), self._observation_refs(sources.observation, sources.evaluation, sources.recovery_proposal)),
            "last_action_result": (self._last_action_state(sources.last_step, sources.result_summary), self._last_action_refs(sources.last_step, sources.result_summary)),
            "memory_context": (safe_memory, self._memory_refs(memory)),
            "budget": (self._budget_fields(sources.attempt), (f"attempt:{getattr(sources.attempt, 'attempt_id', 'none')}",)),
        }
        sections = self._sections(section_inputs)
        sections, omitted = self._truncate_sections(sections)
        project_snapshot_hash = sections.project_evidence.source_hash
        memory_hash = stable_hash(safe_memory) if safe_memory else None
        section_hashes = {
            name: self._section_hash(getattr(sections, name))
            for name in self.SECTION_ORDER
        }
        context_hash = stable_hash(
            {
                "schema_version": 2,
                "lifecycle_id": lifecycle.lifecycle_id,
                "project_id": lifecycle.project_id,
                "section_hashes": section_hashes,
                "omitted_fields": omitted,
                "policy_version": sources.policy_version,
                "redaction_policy_version": sources.redaction_policy_version,
                "prompt_template_version": sources.prompt_template_version,
                "skill_refs": [reference.model_dump(mode="json") for reference in sources.skill_refs],
                "skill_error_codes": list(sources.skill_error_codes),
            }
        )
        return AgentHarnessContext(
            context_hash=context_hash,
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            sections=sections,
            section_hashes=section_hashes,
            memory_context_hash=memory_hash,
            project_snapshot_hash=project_snapshot_hash,
            policy_version=sources.policy_version,
            redaction_policy_version=sources.redaction_policy_version,
            prompt_template_version=sources.prompt_template_version,
            skill_refs=tuple(sorted(set(sources.skill_refs), key=lambda reference: reference.skill_id)),
            skill_error_codes=tuple(sorted(set(sources.skill_error_codes))),
            omitted_fields=tuple(omitted),
        )

    def _sections(self, values: dict[str, tuple[dict[str, Any], tuple[str, ...]]]) -> AgentHarnessContextSections:
        sections = {
            name: AgentHarnessContextSection(
                source_refs=tuple(sorted({ref for ref in refs if ref})),
                source_hash=stable_hash(data),
                data=data,
            )
            for name, (data, refs) in values.items()
        }
        return AgentHarnessContextSections(**sections)

    def _truncate_sections(self, sections: AgentHarnessContextSections) -> tuple[AgentHarnessContextSections, list[str]]:
        """Apply deterministic, section-aware omission without truncating IDs/hashes."""
        candidate = sections
        omitted: list[str] = []
        for name in ("memory_context", "project_evidence", "execution_state", "plan_state", "latest_observation"):
            if self._size_sections(candidate) <= self.MAX_BYTES:
                break
            section = getattr(candidate, name)
            if section.data:
                candidate = candidate.model_copy(update={name: section.model_copy(update={"data": {}})})
                omitted.append(name)
        if self._size_sections(candidate) > self.MAX_BYTES:
            updates = {
                name: getattr(candidate, name).model_copy(update={"data": {}})
                for name in self.SECTION_ORDER
                if name not in self.REQUIRED_SECTIONS
            }
            candidate = candidate.model_copy(update=updates)
            if "nonessential_context" not in omitted:
                omitted.append("nonessential_context")
        if self._size_sections(candidate) > self.MAX_BYTES:
            raise AgentContextLimitExceededError(AgentContextLimitExceededError.code)
        return candidate, omitted

    @staticmethod
    def _size(value: object) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def _size_sections(self, sections: AgentHarnessContextSections) -> int:
        return self._size({"sections": sections.model_dump(mode="json")})

    @staticmethod
    def _section_hash(section: AgentHarnessContextSection) -> str:
        return stable_hash(section.model_dump(mode="json"))

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
                "source_refs": [ref.model_dump(mode="json") for ref in snapshot.source_refs],
            },
            max_items=24,
        )

    def _plan_state(self, lifecycle, plan) -> dict[str, Any]:
        payload = getattr(plan, "payload", {}) if plan is not None else {}
        nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        return {
            "reviewed_plan_id": self._safe_ref(getattr(plan, "reviewed_plan_id", None) or lifecycle.reviewed_plan_id),
            "plan_hash": self._safe_ref(getattr(plan, "plan_hash", None)),
            "revision_no": self._safe_scalar(getattr(plan, "revision_no", None)),
            "revision_reason": self._safe_scalar(getattr(plan, "revision_reason", None)),
            "node_count": len(nodes) if isinstance(nodes, list) else 0,
            "nodes": self._safe_nodes(nodes),
        }

    def _execution_state(self, lifecycle, run_link) -> dict[str, Any]:
        return {
            "execution_ticket_id": self._safe_ref(lifecycle.execution_ticket_id),
            "run_id": self._safe_ref(getattr(run_link, "run_id", None) or lifecycle.run_id),
            "status": self._safe_scalar(getattr(run_link, "status", None)),
            "dispatch_id": self._safe_ref(getattr(run_link, "dispatch_id", None)),
        }

    def _observation_state(self, observation, evaluation, proposal) -> dict[str, Any]:
        return self._safe_object({
            "observation": self._safe_summary(observation),
            "evaluation": self._safe_summary(evaluation),
            "recovery": self._safe_summary(proposal),
        }, max_items=24)

    def _last_action_state(self, step, result_summary) -> dict[str, Any]:
        return self._safe_object({
            "step_id": self._safe_ref(getattr(step, "step_id", None)),
            "kind": self._safe_scalar(getattr(step, "kind", None)),
            "output_hash": self._safe_ref(getattr(step, "output_hash", None)),
            "result_code": self._safe_scalar(getattr(step, "action_result_code", None) or getattr(step, "error_code", None)),
            "summary": self._short_text(getattr(step, "summary", ""), limit=256),
            "result_summary": self._safe_summary(result_summary),
        }, max_items=24)

    def _safe_nodes(self, nodes: object) -> list[dict[str, str]]:
        if not isinstance(nodes, list):
            return []
        result: list[dict[str, str]] = []
        for node in nodes[:24]:
            if not isinstance(node, dict):
                continue
            node_id = self._safe_ref(node.get("id") or node.get("node_id"))
            backend = self._safe_ref(node.get("backend"))
            if node_id:
                result.append({"id": node_id, **({"backend": backend} if backend else {})})
        return result

    def _snapshot_refs(self, snapshot, project_id: str) -> tuple[str, ...]:
        if snapshot is None:
            return (f"project:{project_id}",)
        return tuple(sorted({f"evidence_snapshot:{snapshot.snapshot_hash}", *self._refs_from(getattr(snapshot, "source_refs", ())) }))

    def _plan_refs(self, lifecycle, plan) -> tuple[str, ...]:
        plan_id = getattr(plan, "reviewed_plan_id", None) or lifecycle.reviewed_plan_id
        plan_hash = getattr(plan, "plan_hash", None)
        return tuple(value for value in (self._typed_ref("reviewed_plan", plan_id), self._typed_ref("plan_hash", plan_hash)) if value)

    def _execution_refs(self, lifecycle, run_link) -> tuple[str, ...]:
        return tuple(value for value in (
            self._typed_ref("ticket", lifecycle.execution_ticket_id),
            self._typed_ref("run", getattr(run_link, "run_id", None) or lifecycle.run_id),
            self._typed_ref("dispatch", getattr(run_link, "dispatch_id", None)),
        ) if value)

    def _observation_refs(self, observation, evaluation, proposal) -> tuple[str, ...]:
        return tuple(value for value in (
            self._typed_ref("observation", getattr(observation, "observation_id", None)),
            self._typed_ref("evaluation", getattr(evaluation, "goal_evaluation_id", None)),
            self._typed_ref("recovery", getattr(proposal, "recovery_proposal_id", None)),
        ) if value)

    def _last_action_refs(self, step, result_summary) -> tuple[str, ...]:
        return tuple(value for value in (
            self._typed_ref("harness_step", getattr(step, "step_id", None)),
            self._typed_ref("result", getattr(result_summary, "result_hash", None)),
        ) if value)

    def _memory_refs(self, memory: object) -> tuple[str, ...]:
        if not isinstance(memory, dict):
            return ()
        return tuple(sorted(
            ref for ref in (self._typed_ref("memory", item) for item in memory.get("memory_ids", [])[:24] if isinstance(item, str)) if ref
        ))

    def _refs_from(self, refs: object) -> tuple[str, ...]:
        result: list[str] = []
        for ref in refs if isinstance(refs, list | tuple) else ():
            source_type = self._safe_ref(getattr(ref, "source_type", None))
            source_id = self._safe_ref(getattr(ref, "source_id", None))
            if source_type and source_id:
                result.append(f"{source_type}:{source_id}")
        return tuple(result)

    def _typed_ref(self, kind: str, value: object) -> str | None:
        safe = self._safe_ref(value)
        return f"{kind}:{safe}" if safe else None

    def _safe_summary(self, record) -> dict[str, Any]:
        if record is None:
            return {}
        summary_method = getattr(record, "summary", None)
        summary = summary_method() if callable(summary_method) else record
        if hasattr(summary, "model_dump"):
            summary = summary.model_dump(mode="json")
        return self._safe_object(summary, max_items=24)

    @staticmethod
    def _budget_fields(attempt) -> dict[str, int | str | None | dict[str, int]]:
        if attempt is None:
            return {}
        return {
            "steps_used": attempt.steps_used,
            "model_calls_used": attempt.model_calls_used,
            "action_proposals_used": attempt.action_proposals_used,
            "repairs_used": attempt.repairs_used,
            "recovery_attempts_used": attempt.recovery_attempts_used,
            "input_tokens_used": attempt.input_tokens_used,
            "output_tokens_used": attempt.output_tokens_used,
            "model_call_phase_allocations": attempt.model_call_phase_allocations,
            "model_call_phase_usage": attempt.model_call_phase_usage,
            "deadline_at": attempt.deadline_at.isoformat(),
        }

    def _memory_fields(self, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        allowed = {"context_hash", "memory_ids", "planner_constraints", "decision_suggestions", "evidence_refs", "status", "warning_codes"}
        return self._safe_object({key: value.get(key) for key in allowed if key in value}, max_items=24)

    def _safe_object(self, value: object, *, max_items: int) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key in sorted(value)[:max_items]:
                key_text = str(key)
                if self._SECRET_KEY.search(key_text) or self._UNSAFE_KEY.search(key_text):
                    continue
                if re.search(r"(?:id|hash|ref)$", key_text, re.I) and isinstance(value[key], str):
                    safe_ref = self._safe_ref(value[key])
                    if safe_ref is not None:
                        result[key_text] = safe_ref
                    continue
                result[key_text] = self._safe_object(value[key], max_items=max_items)
            return result
        if isinstance(value, list | tuple):
            return [self._safe_object(item, max_items=max_items) for item in value[:max_items]]
        if isinstance(value, str):
            return self._short_text(value)
        if isinstance(value, int | float | bool) or value is None:
            return value
        return self._short_text(str(value))

    @staticmethod
    def _short_text(value: object, limit: int = 1024) -> str:
        return HarnessContextBuilder._ABSOLUTE_PATH.sub(
            "project://redacted", str(value or "").replace("\x00", "")
        )[:limit]

    @staticmethod
    def _safe_scalar(value: object) -> str | int | float | bool | None:
        if isinstance(value, str):
            sanitized = HarnessContextBuilder._short_text(value, limit=257)
            return sanitized if len(sanitized) <= 256 else None
        return value if isinstance(value, int | float | bool) or value is None else None

    @staticmethod
    def _safe_ref(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.replace("\x00", "")
        if not value or len(value) > 256 or re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", value):
            return None
        return value
