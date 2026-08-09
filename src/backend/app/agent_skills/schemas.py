"""Strict schemas for built-in Agent Product Skills."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentSkillActionKind = Literal[
    "read_evidence", "request_decision", "draft_plan", "explain_result", "propose_recovery", "finish"
]
AgentSkillContextSectionName = Literal[
    "goal", "policy", "project_evidence", "decision_state", "plan_state", "execution_state",
    "latest_observation", "last_action_result", "memory_context", "budget",
]


class SkillManifest(BaseModel):
    """Validated, static metadata for one packaged working procedure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.v[1-9][0-9]*$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    allowed_actions: tuple[AgentSkillActionKind, ...] = Field(min_length=1, max_length=6)
    allowed_states: tuple[str, ...] = Field(min_length=1, max_length=16)
    required_context_sections: tuple[AgentSkillContextSectionName, ...] = Field(
        min_length=1, max_length=10
    )
    output_schema_ref: Literal["ActionEnvelope"]
    max_bytes: int = Field(ge=1, le=16 * 1024)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SkillContextRef(BaseModel):
    """The only Skill data persisted with Context and Harness audit records."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]*\.v[1-9][0-9]*$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sections: tuple[AgentSkillContextSectionName, ...] = Field(min_length=1, max_length=10)
