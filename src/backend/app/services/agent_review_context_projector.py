"""Pure, redacting purpose projections shared by the G0 runner and Team runtime."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.app.schemas.agent_eval import AgentReviewerKind, MultiAgentEvalCase
from src.backend.app.services.agent_review_role_registry import role_for

_FORBIDDEN = re.compile(r"rawdata|dicom|nifti|\bbids\b|api[_ -]?key|credential|password|token=|[a-z]:[\\/]|/(?:home|users|mnt)/", re.IGNORECASE)


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
        "science": ("goal", "capability", "evidence"),
        "safety": ("goal", "policy", "scope", "environment"),
        "completeness": ("goal", "decision_state", "evidence", "coverage"),
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


def context_projector_hash() -> str:
    return hashlib.sha256(b"agent-review-context-projector-v1:fixed-purpose-sections").hexdigest()
