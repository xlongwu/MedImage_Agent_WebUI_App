"""Build immutable, purpose-scoped, redacted Context v3 snapshots for Harness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import (
    AgentContextPurpose,
    AgentHarnessContext,
    AgentHarnessContextSection,
    AgentHarnessContextSections,
)


class AgentContextIncompleteError(ValueError):
    """Raised when a model-facing projection has no complete required context."""


class AgentContextLimitExceededError(ValueError):
    """Base safe-stop error retained for the Harness call boundary."""


class AgentContextRequiredSectionTooLargeError(AgentContextLimitExceededError):
    code = "AGENT_CONTEXT_REQUIRED_SECTION_TOO_LARGE"


@dataclass(frozen=True)
class AgentContextProjectionPolicy:
    """Fixed projection rules; this is deliberately not user-configurable."""

    version: str = "agent-context-projection-v3"
    max_bytes: int = 32 * 1024
    max_items_per_section: int = 24
    required_by_purpose: dict[str, tuple[str, ...]] | None = None
    optional_order_by_purpose: dict[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        if self.required_by_purpose is None:
            object.__setattr__(self, "required_by_purpose", {
                "decision_request": ("goal", "policy", "project_evidence", "decision_state"),
                "plan_draft": ("goal", "policy", "project_evidence", "decision_state"),
            })
        if self.optional_order_by_purpose is None:
            object.__setattr__(self, "optional_order_by_purpose", {
                "decision_request": (
                    "memory_context", "latest_observation", "last_action_result",
                    "plan_state", "execution_state", "budget",
                ),
                "plan_draft": (
                    "memory_context", "last_action_result", "plan_state",
                    "latest_observation", "execution_state", "budget",
                ),
            })


@dataclass(frozen=True)
class HarnessContextSources:
    """Explicit, already-read inputs to the sole Context v3 builder."""

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
    purpose: AgentContextPurpose | None = None
    prompt_template_version: str = "agent-harness-prompt-v3"
    skill_refs: tuple[SkillContextRef, ...] = ()
    skill_error_codes: tuple[str, ...] = ()
    policy_version: str = "agent-harness-policy-v3"
    redaction_policy_version: str = "agent-harness-redaction-v3"


class HarnessContextBuilder:
    """The sole Context builder; it never opens raw files or full logs."""

    MAX_BYTES = 32 * 1024
    _SECRET_KEY = re.compile(r"(?:api[_-]?key|token|secret|password|authorization)", re.I)
    _UNSAFE_KEY = re.compile(r"(?:rawdata|image|dicom|nifti|transcript|prompt|log)", re.I)
    _ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<![:/])/)[^\s,;\]\)}]+")
    SECTION_ORDER = (
        "goal", "policy", "project_evidence", "decision_state", "plan_state",
        "execution_state", "latest_observation", "last_action_result", "memory_context", "budget",
    )

    def __init__(self, policy: AgentContextProjectionPolicy | None = None) -> None:
        self.policy = policy or AgentContextProjectionPolicy(max_bytes=self.MAX_BYTES)

    def purpose_for(self, lifecycle) -> AgentContextPurpose:
        if getattr(lifecycle, "pending_decision_batch", None) is not None or getattr(lifecycle, "state", "") in {
            "WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"
        }:
            return "decision_request"
        return "plan_draft"

    def build(self, *, sources: HarnessContextSources) -> AgentHarnessContext:
        lifecycle = sources.lifecycle
        purpose = sources.purpose or self.purpose_for(lifecycle)
        metadata = getattr(sources.project, "metadata", {}) if sources.project is not None else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        command_context = getattr(lifecycle, "command_context", {})
        command_context = command_context if isinstance(command_context, dict) else {}
        memory = command_context.get("memory_context")
        safe_memory = self._memory_fields(memory)
        snapshot = sources.evidence_snapshot
        section_inputs: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {
            "goal": ({
                "goal": self._short_text(getattr(lifecycle, "goal_text", ""), limit=512),
                "goal_contract_id": self._safe_ref(getattr(lifecycle, "goal_contract_id", None)),
                "goal_contract_hash": self._safe_ref(
                    getattr(lifecycle, "goal_contract_hash", None) or command_context.get("goal_contract_hash")
                ),
                "goal_version": self._safe_scalar(command_context.get("goal_revision")),
                "lifecycle_state": self._safe_scalar(getattr(lifecycle, "state", None)),
            }, (self._typed_ref("lifecycle", getattr(lifecycle, "lifecycle_id", None)) or "",)),
            "policy": (self._policy_fields(purpose), ("policy:agent-context-v3",)),
            "project_evidence": (
                self._safe_snapshot(snapshot) if snapshot is not None else self._safe_project_evidence(metadata, lifecycle),
                self._snapshot_refs(snapshot, getattr(lifecycle, "project_id", "")),
            ),
            "decision_state": ({
                "confirmed_answers": self._safe_object(command_context.get("science_answers"), max_items=12),
                "unresolved_gaps": self._safe_list(getattr(snapshot, "missing", ()), max_items=12),
                "pending_batch_id": self._safe_ref(getattr(getattr(lifecycle, "pending_decision_batch", None), "batch_id", None)),
                "pending_item_count": len(getattr(getattr(lifecycle, "pending_decision_batch", None), "items", ()) or ()),
            }, (self._typed_ref("lifecycle", getattr(lifecycle, "lifecycle_id", None)) or "",)),
            "plan_state": (self._plan_state(lifecycle, sources.reviewed_plan), self._plan_refs(lifecycle, sources.reviewed_plan)),
            "execution_state": (self._execution_state(lifecycle, sources.run_link), self._execution_refs(lifecycle, sources.run_link)),
            "latest_observation": (self._observation_state(sources.observation, sources.evaluation, sources.recovery_proposal), self._observation_refs(sources.observation, sources.evaluation, sources.recovery_proposal)),
            "last_action_result": (self._last_action_state(sources.last_step, sources.result_summary), self._last_action_refs(sources.last_step, sources.result_summary)),
            "memory_context": (safe_memory, self._memory_refs(memory)),
            "budget": (self._budget_fields(sources.attempt), (self._typed_ref("attempt", getattr(sources.attempt, "attempt_id", None)) or "",)),
        }
        sections = self._sections(section_inputs)
        required = tuple(self.policy.required_by_purpose[purpose])
        included, omitted = self._included_sections(sections, purpose, required)
        incomplete_reason = self._incomplete_reason(sections, required)
        if incomplete_reason is None:
            included, omitted = self._fit_optional_sections(sections, included, required, purpose, omitted)
        evidence_refs = self._evidence_refs(snapshot)
        memory_hash = self._safe_ref(safe_memory.get("context_hash"))
        section_hashes = {name: self._section_hash(getattr(sections, name)) for name in self.SECTION_ORDER}
        context_hash = stable_hash({
            "schema_version": 3, "lifecycle_id": getattr(lifecycle, "lifecycle_id", None),
            "project_id": getattr(lifecycle, "project_id", None), "purpose": purpose,
            "required_sections": required, "included_sections": included,
            "omitted_sections": omitted, "evidence_refs": evidence_refs,
            "evidence_snapshot_hash": getattr(snapshot, "snapshot_hash", None),
            "projection_policy_version": self.policy.version, "complete": incomplete_reason is None,
            "incomplete_reason": incomplete_reason,
            "included_section_hashes": {name: section_hashes[name] for name in included},
            "policy_version": sources.policy_version, "redaction_policy_version": sources.redaction_policy_version,
            "prompt_template_version": sources.prompt_template_version,
            "tool_catalog_version": self._policy_fields(purpose).get("tool_catalog_version"),
            "memory_context_hash": memory_hash,
            "skill_refs": [reference.model_dump(mode="json") for reference in sorted(set(sources.skill_refs), key=lambda item: item.skill_id)],
            "skill_error_codes": sorted(set(sources.skill_error_codes)),
        })
        return AgentHarnessContext(
            context_hash=context_hash, lifecycle_id=getattr(lifecycle, "lifecycle_id"),
            project_id=getattr(lifecycle, "project_id"), purpose=purpose, sections=sections,
            section_hashes=section_hashes, required_sections=required, included_sections=included,
            omitted_sections=tuple(omitted), evidence_refs=tuple(evidence_refs),
            evidence_snapshot_hash=self._safe_ref(getattr(snapshot, "snapshot_hash", None)),
            projection_policy_version=self.policy.version, complete=incomplete_reason is None,
            incomplete_reason=incomplete_reason, memory_context_hash=memory_hash,
            project_snapshot_hash=sections.project_evidence.source_hash,
            policy_version=sources.policy_version, redaction_policy_version=sources.redaction_policy_version,
            prompt_template_version=sources.prompt_template_version,
            skill_refs=tuple(sorted(set(sources.skill_refs), key=lambda item: item.skill_id)),
            skill_error_codes=tuple(sorted(set(sources.skill_error_codes))),
        )

    def _included_sections(self, sections, purpose: AgentContextPurpose, required: tuple[str, ...]) -> tuple[tuple[str, ...], list[str]]:
        included = list(required)
        for name in self.policy.optional_order_by_purpose[purpose]:
            if self._has_meaningful_data(getattr(sections, name).data):
                included.append(name)
        return tuple(dict.fromkeys(included)), []

    def _fit_optional_sections(self, sections, included, required, purpose, omitted):
        candidate = list(included)
        for name in self.policy.optional_order_by_purpose[purpose]:
            if self._payload_size(sections, candidate) <= self.MAX_BYTES:
                break
            if name in candidate and name not in required:
                candidate.remove(name)
                omitted.append(f"{name}:byte_budget")
        if self._payload_size(sections, candidate) > self.MAX_BYTES:
            raise AgentContextRequiredSectionTooLargeError(AgentContextRequiredSectionTooLargeError.code)
        return tuple(candidate), omitted

    def _incomplete_reason(self, sections, required: tuple[str, ...]) -> str | None:
        for name in required:
            if name == "goal" and not str(getattr(sections, name).data.get("goal") or "").strip():
                return "AGENT_CONTEXT_REQUIRED_SECTION_MISSING:goal"
            if name == "decision_state":
                # An explicitly empty answer/gap set is a valid, auditable
                # decision state; it must not be confused with a missing section.
                continue
            if not self._has_meaningful_data(getattr(sections, name).data):
                return f"AGENT_CONTEXT_REQUIRED_SECTION_MISSING:{name}"
        return None

    def _payload_size(self, sections, included: list[str]) -> int:
        return self._size({"schema_version": 3, "sections": {
            name: getattr(sections, name).model_dump(mode="json") for name in included
        }})

    def _sections(self, values):
        return AgentHarnessContextSections(**{
            name: AgentHarnessContextSection(
                source_refs=tuple(sorted({ref for ref in refs if ref})), source_hash=stable_hash(data), data=data,
            ) for name, (data, refs) in values.items()
        })

    def _policy_fields(self, purpose: AgentContextPurpose) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action_allowlist": ["request_decision", "draft_plan"], "rawdata_read_only": True,
            "approved_write_roots": ["work", "logs", "reports", "derivatives", "exports"],
            "plan_only": True, "projection_policy_version": self.policy.version,
            "tool_catalog_version": "node-contract-tool-catalog-v1",
        }
        if purpose == "plan_draft":
            from src.backend.app.runtime.tool_catalog import build_tool_catalog
            catalog = build_tool_catalog()
            result["allowed_nodes"] = [{"id": item.id, "backend": item.backend} for item in catalog]
            result["tool_catalog_hash"] = stable_hash(result["allowed_nodes"])
        return result

    def _safe_project_evidence(self, metadata: dict[str, Any], lifecycle) -> dict[str, Any]:
        allowed = {"data_state", "subject_count", "dataset_type", "registered_artifact_count", "conversion_status", "preprocessing_status"}
        data = self._safe_object({key: metadata.get(key) for key in allowed if key in metadata}, max_items=24)
        data["project_id"] = self._safe_ref(getattr(lifecycle, "project_id", None))
        return data

    def _safe_snapshot(self, snapshot) -> dict[str, Any]:
        return self._safe_object({
            "snapshot_hash": getattr(snapshot, "snapshot_hash", None),
            "facts": [item.model_dump(mode="json") for item in getattr(snapshot, "facts", ())[:self.policy.max_items_per_section]],
            "missing": list(getattr(snapshot, "missing", ())[:self.policy.max_items_per_section]),
            "warnings": [item.model_dump(mode="json") for item in getattr(snapshot, "warnings", ())[:self.policy.max_items_per_section]],
            "source_refs": [item.model_dump(mode="json") for item in getattr(snapshot, "source_refs", ())[:self.policy.max_items_per_section]],
        }, max_items=self.policy.max_items_per_section)

    def _evidence_refs(self, snapshot) -> list[dict[str, str]]:
        if snapshot is None:
            return []
        result = []
        for ref in getattr(snapshot, "source_refs", ())[:self.policy.max_items_per_section]:
            record_type = self._safe_ref(getattr(ref, "source_type", None))
            record_id = self._safe_ref(getattr(ref, "source_id", None))
            content_hash = self._safe_ref(getattr(ref, "source_hash", None))
            if record_type and record_id and content_hash:
                result.append({"type": record_type, "record_id": record_id, "hash": content_hash})
        return sorted(result, key=lambda item: (item["type"], item["record_id"], item["hash"]))

    def _plan_state(self, lifecycle, plan) -> dict[str, Any]:
        payload = getattr(plan, "payload", {}) if plan is not None else {}
        nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
        return {"reviewed_plan_id": self._safe_ref(getattr(plan, "reviewed_plan_id", None) or getattr(lifecycle, "reviewed_plan_id", None)), "plan_hash": self._safe_ref(getattr(plan, "plan_hash", None)), "revision_no": self._safe_scalar(getattr(plan, "revision_no", None)), "node_count": len(nodes) if isinstance(nodes, list) else 0, "nodes": self._safe_nodes(nodes)}

    def _execution_state(self, lifecycle, run_link) -> dict[str, Any]:
        return {"run_id": self._safe_ref(getattr(run_link, "run_id", None) or getattr(lifecycle, "run_id", None)), "status": self._safe_scalar(getattr(run_link, "status", None)), "dispatch_id": self._safe_ref(getattr(run_link, "dispatch_id", None))}

    def _observation_state(self, observation, evaluation, proposal) -> dict[str, Any]:
        return self._safe_object({"observation": self._safe_summary(observation), "evaluation": self._safe_summary(evaluation), "recovery": self._safe_summary(proposal)}, max_items=24)

    def _last_action_state(self, step, result_summary) -> dict[str, Any]:
        return self._safe_object({"step_id": self._safe_ref(getattr(step, "step_id", None)), "kind": self._safe_scalar(getattr(step, "kind", None)), "action_hash": self._safe_ref(getattr(step, "action_hash", None)), "result_code": self._safe_scalar(getattr(step, "action_result_code", None) or getattr(step, "error_code", None)), "summary": self._short_text(getattr(step, "summary", ""), limit=256), "result_summary": self._safe_summary(result_summary)}, max_items=24)

    def _safe_nodes(self, nodes: object) -> list[dict[str, str]]:
        result = []
        for node in nodes[:self.policy.max_items_per_section] if isinstance(nodes, list) else []:
            if isinstance(node, dict) and (node_id := self._safe_ref(node.get("id") or node.get("node_id"))):
                result.append({"id": node_id, **({"backend": backend} if (backend := self._safe_ref(node.get("backend"))) else {})})
        return result

    def _snapshot_refs(self, snapshot, project_id: str) -> tuple[str, ...]:
        if snapshot is None:
            return (f"project:{project_id}",) if project_id else ()
        return tuple(sorted({f"evidence_snapshot:{getattr(snapshot, 'snapshot_hash', '')}", *self._refs_from(getattr(snapshot, "source_refs", ())) }))

    def _plan_refs(self, lifecycle, plan) -> tuple[str, ...]:
        return tuple(item for item in (self._typed_ref("reviewed_plan", getattr(plan, "reviewed_plan_id", None) or getattr(lifecycle, "reviewed_plan_id", None)), self._typed_ref("plan_hash", getattr(plan, "plan_hash", None))) if item)

    def _execution_refs(self, lifecycle, run_link) -> tuple[str, ...]:
        return tuple(item for item in (self._typed_ref("run", getattr(run_link, "run_id", None) or getattr(lifecycle, "run_id", None)), self._typed_ref("dispatch", getattr(run_link, "dispatch_id", None))) if item)

    def _observation_refs(self, observation, evaluation, proposal) -> tuple[str, ...]:
        return tuple(item for item in (self._typed_ref("observation", getattr(observation, "observation_id", None)), self._typed_ref("evaluation", getattr(evaluation, "goal_evaluation_id", None)), self._typed_ref("recovery", getattr(proposal, "recovery_proposal_id", None))) if item)

    def _last_action_refs(self, step, result_summary) -> tuple[str, ...]:
        return tuple(item for item in (self._typed_ref("harness_step", getattr(step, "step_id", None)), self._typed_ref("result", getattr(result_summary, "result_hash", None))) if item)

    def _memory_refs(self, memory: object) -> tuple[str, ...]:
        return tuple(sorted(ref for ref in (self._typed_ref("memory", item) for item in memory.get("memory_ids", [])[:24] if isinstance(item, str)) if ref)) if isinstance(memory, dict) else ()

    def _refs_from(self, refs: object) -> tuple[str, ...]:
        return tuple(f"{kind}:{record_id}" for ref in refs if (kind := self._safe_ref(getattr(ref, "source_type", None))) and (record_id := self._safe_ref(getattr(ref, "source_id", None)))) if isinstance(refs, (list, tuple)) else ()

    def _safe_summary(self, record) -> dict[str, Any]:
        summary = getattr(record, "summary", None)
        value = summary() if callable(summary) else record
        return self._safe_object(value.model_dump(mode="json") if hasattr(value, "model_dump") else value, max_items=24) if value is not None else {}

    @staticmethod
    def _budget_fields(attempt) -> dict[str, Any]:
        return {} if attempt is None else {"steps_used": attempt.steps_used, "model_calls_used": attempt.model_calls_used, "action_proposals_used": attempt.action_proposals_used, "repairs_used": attempt.repairs_used, "deadline_at": attempt.deadline_at.isoformat()}

    def _memory_fields(self, value: object) -> dict[str, Any]:
        allowed = {"context_hash", "memory_ids", "planner_constraints", "decision_suggestions", "evidence_refs", "status", "warning_codes"}
        return self._safe_object({key: value.get(key) for key in allowed if key in value}, max_items=24) if isinstance(value, dict) else {}

    @staticmethod
    def _size(value: object) -> int:
        return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _section_hash(section: AgentHarnessContextSection) -> str:
        return stable_hash(section.model_dump(mode="json"))

    @staticmethod
    def _has_meaningful_data(value: object) -> bool:
        if isinstance(value, dict):
            return any(HarnessContextBuilder._has_meaningful_data(item) for item in value.values())
        if isinstance(value, (list, tuple, set)): return bool(value)
        return value not in (None, "", 0, False)

    def _safe_object(self, value: object, *, max_items: int) -> Any:
        if isinstance(value, dict):
            return {str(key): self._safe_object(value[key], max_items=max_items) for key in sorted(value)[:max_items] if not self._SECRET_KEY.search(str(key)) and not self._UNSAFE_KEY.search(str(key))}
        if isinstance(value, (list, tuple)): return [self._safe_object(item, max_items=max_items) for item in value[:max_items]]
        if isinstance(value, str): return self._short_text(value)
        return value if isinstance(value, (int, float, bool)) or value is None else self._short_text(str(value))

    def _safe_list(self, value: object, *, max_items: int) -> list[Any]:
        return self._safe_object(list(value), max_items=max_items) if isinstance(value, (list, tuple)) else []

    @staticmethod
    def _short_text(value: object, limit: int = 1024) -> str:
        return HarnessContextBuilder._ABSOLUTE_PATH.sub("project://redacted", str(value or "").replace("\x00", ""))[:limit]

    @staticmethod
    def _safe_scalar(value: object) -> str | int | float | bool | None:
        return HarnessContextBuilder._short_text(value, limit=256) if isinstance(value, str) else value if isinstance(value, (int, float, bool)) or value is None else None

    @staticmethod
    def _safe_ref(value: object) -> str | None:
        return value.replace("\x00", "") if isinstance(value, str) and value and len(value) <= 256 and not re.match(r"^(?:[A-Za-z]:[\\/]|/|\\\\)", value) else None

    def _typed_ref(self, kind: str, value: object) -> str | None:
        return f"{kind}:{safe}" if (safe := self._safe_ref(value)) else None
