"""Pure, redacting purpose projections shared by the G0 runner and Team runtime."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.app.schemas.agent_eval import AgentReviewerKind, MultiAgentEvalCase
from src.backend.app.schemas.agent_harness import (
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    AgentTeamRoleContext,
    agent_team_role_context_hash,
)
from src.backend.app.schemas.agent_team import AgentTeamBudgetSlice
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.agent_review_role_registry import role_for

_FORBIDDEN = re.compile(r"rawdata|dicom|nifti|\bbids\b|api[_ -]?key|credential|password|token=|[a-z]:[\\/]|/(?:home|users|mnt)/", re.IGNORECASE)


def _contains_forbidden_context(
    value: object,
    *,
    registered_tool_nodes: frozenset[tuple[str, str]] = frozenset(),
) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            # This fixed policy flag is safe and must be visible to reviewers.
            if key == "rawdata_read_only" and child is True:
                continue
            if key == "allowed_nodes" and isinstance(child, list):
                if all(
                    isinstance(item, dict)
                    and set(item) == {"id", "backend"}
                    and (item.get("id"), item.get("backend")) in registered_tool_nodes
                    for item in child
                ):
                    # These exact ids/backends come from the code-owned catalog;
                    # names such as "bids_validation_check" are not user data.
                    continue
            if _FORBIDDEN.search(str(key)) or _contains_forbidden_context(
                child, registered_tool_nodes=registered_tool_nodes
            ):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(
            _contains_forbidden_context(
                item, registered_tool_nodes=registered_tool_nodes
            )
            for item in value
        )
    return isinstance(value, str) and _FORBIDDEN.search(value) is not None


class AgentReviewRoleContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = 1
    reviewer_kind: AgentReviewerKind
    role_id: str
    purpose: str
    case_id: str
    source_ref_hash: str
    base_context_hash: str
    included_sections: tuple[str, ...]
    omitted_sections: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    goal_summary: str = Field(min_length=1, max_length=240)
    role_context_hash: str

    @model_validator(mode="after")
    def no_sensitive_content(self) -> AgentReviewRoleContext:
        value = "\n".join((self.goal_summary, *self.evidence_refs, *self.included_sections, *self.omitted_sections))
        if _FORBIDDEN.search(value):
            raise ValueError("MULTI_AGENT_REVIEW_CONTEXT_REDACTION_VIOLATION")
        return self


class AgentReviewContextProjector:
    """Build only the fixed-purpose projection; never reads a project filesystem."""

    _SECTIONS = {
        "science": ("goal", "project_evidence", "plan_state", "budget"),
        "safety": ("goal", "policy", "project_evidence", "execution_state"),
        "completeness": (
            "goal", "decision_state", "project_evidence", "plan_state", "last_action_result"
        ),
    }
    _REQUIRED = {
        "science": ("goal", "project_evidence"),
        "safety": ("goal", "policy"),
        "completeness": ("goal", "decision_state", "project_evidence"),
    }

    def project(self, *, case: MultiAgentEvalCase, reviewer_kind: AgentReviewerKind) -> AgentReviewRoleContext:
        role = role_for(reviewer_kind)
        included = self._SECTIONS[reviewer_kind]
        payload = {
            "schema_version": 1,
            "reviewer_kind": reviewer_kind,
            "role_id": role.role_id,
            "purpose": role.purpose,
            "case_id": case.case_id,
            "source_ref_hash": case.source_ref_hash,
            "base_context_hash": case.frozen_context_hash,
            "included_sections": included,
            "omitted_sections": tuple(sorted(set(self._SECTIONS["science"] + self._SECTIONS["safety"] + self._SECTIONS["completeness"]) - set(included))),
            "evidence_refs": tuple(sorted(case.input_refs)),
            "goal_summary": case.goal_summary,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return AgentReviewRoleContext(**payload, role_context_hash=digest)

    def project_base_context(
        self,
        *,
        base_context: AgentPlanningBaseContext,
        team_id: str,
        team_epoch: int,
        reviewer_kind: AgentReviewerKind,
        consent: ProjectMultiAgentConsent,
        model_profile_hash: str,
        budget_slice: AgentTeamBudgetSlice,
        deadline_at: datetime,
    ) -> AgentTeamRoleContext:
        """Build one fixed production role projection from the frozen Base v1."""
        role = role_for(reviewer_kind)
        if not base_context.complete:
            raise ValueError("AGENT_PLANNING_BASE_CONTEXT_INCOMPLETE")
        if (
            not consent.enabled
            or consent.project_id != base_context.project_id
            or deadline_at > budget_slice.deadline_at
        ):
            raise ValueError("MULTI_AGENT_CONSENT_INVALIDATED")
        included = self._SECTIONS[reviewer_kind]
        if not set(self._REQUIRED[reviewer_kind]).issubset(base_context.section_hashes):
            raise ValueError("TEAM_ROLE_CONTEXT_REQUIRED_SECTION_MISSING")
        sections = {
            name: getattr(base_context.sections, name)
            for name in included
        }
        section_payloads = {
            name: section.model_dump(mode="json") for name, section in sections.items()
        }
        registered_tool_nodes = frozenset()
        if "policy" in sections:
            from src.backend.app.runtime.tool_catalog import build_tool_catalog

            registered_tool_nodes = frozenset(
                (item.id, item.backend) for item in build_tool_catalog()
            )
        if _contains_forbidden_context(
            section_payloads, registered_tool_nodes=registered_tool_nodes
        ):
            raise ValueError("MULTI_AGENT_REVIEW_CONTEXT_REDACTION_VIOLATION")
        evidence_hashes: list[str] = []
        for reference in base_context.evidence_refs:
            digest = reference.get("hash") or reference.get("source_hash")
            if not isinstance(digest, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", digest):
                raise ValueError("MULTI_AGENT_REVIEW_INPUT_REF_INVALID")
            evidence_hashes.append(f"evidence:{digest}")
        evidence_refs = tuple(f"section:{name}" for name in included) + tuple(
            sorted(set(evidence_hashes))
        )
        payload = {
            "schema_version": 1,
            "team_id": team_id,
            "team_epoch": team_epoch,
            "worker_id": f"worker-{team_id}-{reviewer_kind}",
            "role_id": role.role_id,
            "reviewer_kind": reviewer_kind,
            "project_id": base_context.project_id,
            "lifecycle_id": base_context.lifecycle_id,
            "base_context_hash": base_context.base_context_hash,
            "purpose": role.purpose,
            "included_sections": included,
            "omitted_sections": tuple(
                name for name in AgentHarnessContextSections.model_fields if name not in included
            ),
            "sections": {
                name: section.model_dump(mode="json") for name, section in sections.items()
            },
            "section_hashes": {name: base_context.section_hashes[name] for name in included},
            "evidence_refs": evidence_refs,
            "model_profile_hash": model_profile_hash,
            "prompt_template_version": role.prompt_template_version,
            "output_schema_version": role.output_schema_version,
            "policy_version": base_context.policy_version,
            "redaction_policy_version": base_context.redaction_policy_version,
            "consent_epoch": consent.consent_epoch,
            "consent_hash": consent.consent_hash,
            "budget_slice": budget_slice.model_dump(mode="json"),
            "deadline_at": deadline_at,
        }
        return AgentTeamRoleContext(
            role_context_hash=agent_team_role_context_hash(payload),
            **{key: value for key, value in payload.items() if key != "schema_version"},
        )


def context_projector_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
