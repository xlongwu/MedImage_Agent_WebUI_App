"""Read only canonical records; never resolve a caller's URI or open a file."""
import re

from src.backend.app.core.exceptions import NotFoundError, SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.reviewed_plan_store import reviewed_plan_identity
from src.backend.app.schemas.agent_plan_evidence import (
    AgentPlanEvidence, AgentPlanEvidenceNode, AgentPlanEvidenceReference,
)


def safe_record_identifier(value) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) and ".." not in value else None


class AgentPlanEvidenceService:
    def __init__(self, store):
        self.store = store

    def get(self, *, project_id: str, task_id: str, plan_hash: str | None = None) -> AgentPlanEvidence:
        task = self.store.get_agent_lifecycle(task_id)
        if task is None or task.project_id != project_id:
            raise NotFoundError("AGENT_PLAN_EVIDENCE_NOT_FOUND", code="AGENT_PLAN_EVIDENCE_NOT_FOUND")
        base = dict(project_id=project_id, task_id=task_id, available=False)
        if task.reviewed_plan_id is None:
            return AgentPlanEvidence(**base, missing_code="AGENT_PLAN_NOT_SAVED")
        record = self.store.get_reviewed_plan(task.reviewed_plan_id)
        if record is None:
            return AgentPlanEvidence(**base, missing_code="AGENT_PLAN_RECORD_MISSING")
        if record.project_id != project_id:
            raise NotFoundError("AGENT_PLAN_EVIDENCE_NOT_FOUND", code="AGENT_PLAN_EVIDENCE_NOT_FOUND")
        if plan_hash is not None and record.plan_hash != plan_hash:
            raise SafetyError("AGENT_PLAN_EVIDENCE_STALE", code="AGENT_PLAN_EVIDENCE_STALE")
        payload = record.payload
        plan = payload.get("plan")
        if not isinstance(plan, dict) or stable_hash(plan) != payload.get("normalized_plan_hash"):
            raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT")
        contract = payload.get("goal_contract")
        if not isinstance(contract, dict):
            raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT")
        try:
            expected_id, expected_hash = reviewed_plan_identity(
                project_id, plan, contract, payload.get("memory_context") or None,
                record.planner_invocation, record.planner_evidence, record.planning_inputs_hash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT") from exc
        if expected_id != record.reviewed_plan_id or expected_hash != record.plan_hash:
            raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT")
        nodes = []
        for node in plan.get("nodes", []):
            node_id = safe_record_identifier(node.get("id")) if isinstance(node, dict) else None
            if node_id is None:
                raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT")
            nodes.append(AgentPlanEvidenceNode(
                node_id=node_id, backend=safe_record_identifier(node.get("backend")),
                depends_on=tuple(value for raw in node.get("depends_on", []) if (value := safe_record_identifier(raw))),
            ))
        refs = []
        snapshot = self.store.get_agent_evidence_snapshot(record.evidence_snapshot_hash) if record.evidence_snapshot_hash else None
        if snapshot is not None:
            if snapshot.project_id != project_id or snapshot.lifecycle_id != task_id:
                raise SafetyError("AGENT_PLAN_EVIDENCE_CORRUPT", code="AGENT_PLAN_EVIDENCE_CORRUPT")
            refs = [AgentPlanEvidenceReference(
                ref_type=ref.source_type, ref_id=identifier, content_hash=ref.source_hash,
            ) for ref in snapshot.source_refs[:64] if (identifier := safe_record_identifier(ref.source_id))]
        scope = contract.get("scope") or {}
        return AgentPlanEvidence(
            project_id=project_id, task_id=task_id, available=True,
            reviewed_plan_id=record.reviewed_plan_id, plan_hash=record.plan_hash,
            revision_no=record.revision_no, parent_reviewed_plan_id=record.parent_reviewed_plan_id,
            parent_plan_hash=record.parent_plan_hash, revision_reason=record.revision_reason,
            goal_kind=safe_record_identifier(contract.get("goal_kind")),
            subject_ids=tuple(identifier for raw in scope.get("subject_ids", []) if (identifier := safe_record_identifier(raw))),
            session_ids=tuple(identifier for raw in scope.get("session_ids", []) if (identifier := safe_record_identifier(raw))),
            include=tuple(identifier for raw in scope.get("include", []) if (identifier := safe_record_identifier(raw))),
            exclude=tuple(identifier for raw in scope.get("exclude", []) if (identifier := safe_record_identifier(raw))),
            completeness_required=bool(scope.get("completeness_required", True)),
            nodes=tuple(nodes), input_refs=tuple(refs),
        )
