"""Safe, project/task-bound read projection of a saved Reviewed Plan."""
from typing import Literal
from pydantic import BaseModel, ConfigDict


class AgentPlanEvidenceNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    node_id: str
    backend: str | None = None
    depends_on: tuple[str, ...] = ()


class AgentPlanEvidenceReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ref_type: str
    ref_id: str
    content_hash: str | None = None


class AgentPlanEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[1] = 1
    project_id: str
    task_id: str
    available: bool
    missing_code: Literal["AGENT_PLAN_NOT_SAVED", "AGENT_PLAN_RECORD_MISSING"] | None = None
    reviewed_plan_id: str | None = None
    plan_hash: str | None = None
    revision_no: int | None = None
    parent_reviewed_plan_id: str | None = None
    parent_plan_hash: str | None = None
    revision_reason: str | None = None
    goal_kind: str | None = None
    subject_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    completeness_required: bool = True
    nodes: tuple[AgentPlanEvidenceNode, ...] = ()
    input_refs: tuple[AgentPlanEvidenceReference, ...] = ()
