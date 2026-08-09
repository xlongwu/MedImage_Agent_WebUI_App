from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import yaml

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.execution_gateway import current_allowlist_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord, RunLinkRecord
from src.backend.app.schemas.goal_contract import (
    CriterionResult,
    GoalContract,
    GoalCriterion,
    GoalEvaluationRecord,
    GoalScope,
)
from src.backend.app.schemas.gateway_dispatch import GatewayDispatch
from src.backend.app.schemas.observation import (
    CapabilityObservation,
    NodeObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    ObservationSourceRef,
    PipelineObservation,
    ScientificObservation,
)
from src.backend.app.schemas.recovery import (
    RecoveryChangeRequest,
    RecoveryQuotaLimits,
    RecoveryQuotaUsage,
)
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.goal_evaluator import calculate_goal_evaluation_hash
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.observation_collector import calculate_observation_hash
from src.backend.app.services.recovery_proposal_engine import RecoveryProposalEngine
from src.backend.app.services.run_diagnosis_service import RunDiagnosisService

QUOTA = {
    "max_lifecycle_recovery_attempts": 2,
    "max_node_attempts": 2,
    "max_subject_node_attempts": 2,
    "max_replans": 1,
    "max_recovery_wall_seconds": 600,
}


def build_recovery_fixture(
    tmp_path: Path,
    *,
    quota: dict[str, int] | None = None,
    changes: RecoveryChangeRequest | None = None,
):
    project_id = f"phase8-{uuid4().hex[:10]}"
    lifecycle_id = f"lifecycle-{uuid4().hex[:10]}"
    project_root = tmp_path / "project"
    rawdata = project_root / "rawdata"
    inputs = project_root / "inputs"
    outputs = project_root / "derivatives"
    for path in (rawdata, inputs, outputs):
        path.mkdir(parents=True, exist_ok=True)
    config_path = project_root / "project.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "phase8-test", "root_dir": str(project_root)},
                "runtime": {
                    "work_dir": str(project_root / "work"),
                    "log_dir": str(project_root / "logs"),
                    "derivatives_dir": str(outputs),
                    "matlab_command": "matlab-disabled",
                },
                "third_party": {
                    "spm_dir": str(project_root / "disabled-spm"),
                    "dpabi_dir": str(project_root / "disabled-dpabi"),
                },
                "safety": {"rawdata_readonly": True},
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "pipeline_id": "phase8-contract-smoke",
        "execution": {"run_id": "parent-run"},
        "nodes": [
            {
                "id": "contract_smoke",
                "name": "Contract smoke",
                "agent": "test",
                "backend": "python",
                "depends_on": [],
                "inputs": [],
                "outputs": [],
                "params": {"fail": False, "message": "recovered"},
                "parallel_level": "project",
                "gpu_supported": False,
                "cache": False,
            }
        ],
    }
    pipeline_path = project_root / "pipeline.yaml"
    pipeline_path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")

    limits = dict(QUOTA)
    if quota:
        limits.update(quota)
    store = SQLiteDesktopStore(tmp_path / "phase8.sqlite")
    store.add_project(
        ProjectDetail(
            id=project_id,
            name="Phase 8 recovery",
            study_id=project_id,
            modality="rs-fMRI",
            created_date="test",
            subjects_count=0,
            current_pipeline_id="phase8-contract-smoke",
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
            metadata={
                "project_dir": str(project_root),
                "rawdata_dir": str(rawdata),
                "project_config_path": str(config_path),
                "recovery_policy": limits,
            },
        ),
        health_status="Ready",
        rawdata_dir=str(rawdata),
    )

    reviewed_plan_id = f"reviewed-{uuid4().hex[:10]}"
    plan_hash = stable_hash(plan)
    goal = GoalContract(
        goal_contract_id=f"goal-{uuid4().hex[:10]}",
        goal_text="Complete the reviewed contract smoke pipeline",
        goal_kind="pipeline_completion",
        project_id=project_id,
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
        scope=GoalScope(completeness_required=True),
        criteria=(
            GoalCriterion(
                criterion_id="pipeline-complete",
                criterion_type="pipeline_terminal",
                target="pipeline",
                expected={"statuses": ["SUCCESS", "COMPLETED"]},
            ),
        ),
        minimum_capability_level="unavailable",
        reviewed_actor="test-reviewer",
        reviewed_at=datetime.now(UTC),
        goal_contract_hash="pending",
    )
    goal_payload = goal.model_dump(mode="json")
    goal_payload.pop("goal_contract_hash")
    goal = goal.model_copy(update={"goal_contract_hash": stable_hash(goal_payload)})
    reviewed = ReviewedPlanRecord(
        reviewed_plan_id=reviewed_plan_id,
        project_id=project_id,
        project_config_path=str(config_path),
        plan_hash=plan_hash,
        plan_path=str(project_root / "plans" / f"{reviewed_plan_id}.json"),
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
        status="REVIEWED",
        payload={
            "plan": plan,
            "normalized_plan_hash": stable_hash(plan),
            "goal_contract": goal.model_dump(mode="json"),
            "goal_contract_status": "reviewed",
        },
    )
    store.add_reviewed_plan(reviewed)

    contract = get_node_contract("contract_smoke")
    ticket_service = ExecutionTicketService(store)
    parent = ticket_service.issue(
        project_id=project_id,
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
        goal_contract_hash=goal.goal_contract_hash,
        evaluation_policy_version=goal.evaluation_policy_version,
        approval_summary_hash="parent-approval",
        memory_context_hash=None,
        approved_actor="test-reviewer",
        approved_node_ids=("contract_smoke",),
        approved_backend_ids=("python",),
        input_roots=(str(inputs),),
        output_roots=(str(outputs),),
        readonly_roots=(str(rawdata),),
        project_config_path=str(config_path),
        pipeline_path=str(pipeline_path),
        allowlist_hash=current_allowlist_hash(),
        normalized_params_hash=stable_hash({"contract_smoke": plan["nodes"][0]["params"]}),
        contract_versions={"contract_smoke": contract.contract_version},
        audit_id="parent-audit",
        max_retry_count=2,
        **limits,
    )
    parent = store.update_execution_ticket(
        parent.execution_ticket_id,
        status="consumed",
        consumed_at=datetime.now(UTC).isoformat(),
        idempotency_key="parent-dispatch",
    )
    assert parent is not None
    dispatch_identity = {
        "schema_version": 1,
        "command_id": "parent-dispatch-command",
        "project_id": project_id,
        "reviewed_plan_id": reviewed_plan_id,
        "execution_ticket_id": parent.execution_ticket_id,
        "approval_summary_hash": parent.approval_summary_hash,
        "plan_hash": parent.plan_hash,
        "memory_context_hash": parent.memory_context_hash,
        "scope_hash": parent.scope_hash,
        "allowlist_hash": parent.allowlist_hash,
        "run_id": "parent-run",
    }
    parent_dispatch = store.add_gateway_dispatch(
        GatewayDispatch(
            dispatch_id="parent-dispatch",
            created_at=datetime.now(UTC),
            canonical_hash=stable_hash(dispatch_identity),
            **{
                key: value
                for key, value in dispatch_identity.items()
                if key != "schema_version"
            },
        )
    )
    now_iso = datetime.now(UTC).isoformat()
    store.add_run_link(
        RunLinkRecord(
            run_link_id=f"runlink-{uuid4().hex[:10]}",
            project_id=project_id,
            reviewed_plan_id=reviewed_plan_id,
            run_id="parent-run",
            dispatch_id=parent_dispatch.dispatch_id,
            pipeline_path=str(pipeline_path),
            project_config_path=str(config_path),
            audit_id=parent.audit_id,
            status="FAILED",
            created_at=now_iso,
            updated_at=now_iso,
        )
    )

    now = datetime.now(UTC)
    observation = ObservationRecord(
        observation_id=f"observation-{uuid4().hex[:10]}",
        bindings=ObservationBindings(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            reviewed_plan_id=reviewed_plan_id,
            plan_hash=plan_hash,
            goal_contract_id=goal.goal_contract_id,
            goal_contract_hash=goal.goal_contract_hash,
            run_id="parent-run",
            execution_ticket_id=parent.execution_ticket_id,
            dispatch_id=parent_dispatch.dispatch_id,
        ),
        collected_at=now,
        sources=(
            ObservationSourceRef(
                source_id="parent-summary",
                source_type="pipeline_summary",
                read_status="ok",
                observed_at=now,
                freshness="fresh",
            ),
            ObservationSourceRef(
                source_id="parent-node-state",
                source_type="node_state",
                read_status="ok",
                observed_at=now,
                freshness="fresh",
            ),
        ),
        pipeline=PipelineObservation(
            status="FAILED",
            nodes_total=1,
            nodes_succeeded=0,
            nodes_failed=1,
            active_nodes=0,
            summary_consistent=True,
            errors=("NODE_FAILED: transient fixture failure",),
            evidence_ids=("parent-summary",),
        ),
        nodes=(
            NodeObservation(
                node_id="contract_smoke",
                status="FAILED",
                backend="python",
                contract_version=contract.contract_version,
                errors=("NODE_FAILED: transient fixture failure",),
                evidence_ids=("parent-node-state",),
            ),
        ),
        capability=CapabilityObservation(
            declared_level=contract.capability_level,
            observed_level="unavailable",
            defensible_level="unavailable",
        ),
        scientific=ScientificObservation(status="unavailable"),
        completeness=ObservationCompleteness(status="complete"),
        observation_hash="pending",
    )
    observation = observation.model_copy(
        update={"observation_hash": calculate_observation_hash(observation)}
    )
    evaluation = GoalEvaluationRecord(
        goal_evaluation_id=f"evaluation-{uuid4().hex[:10]}",
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
        goal_contract_id=goal.goal_contract_id,
        goal_contract_hash=goal.goal_contract_hash,
        observation_id=observation.observation_id,
        observation_hash=observation.observation_hash,
        evaluated_at=now,
        criterion_results=(
            CriterionResult(
                criterion_id="pipeline-complete",
                criterion_type="pipeline_terminal",
                status="failed",
                blocking=True,
                reason_code="PIPELINE_TERMINAL_STATUS_FAILED",
            ),
        ),
        status="not_satisfied",
        goal_evaluation_hash="pending",
    )
    evaluation = evaluation.model_copy(
        update={"goal_evaluation_hash": calculate_goal_evaluation_hash(evaluation)}
    )
    diagnosis = RunDiagnosisService(get_node_contract).build(
        observation=observation,
        evaluation=evaluation,
        ticket=parent,
        created_at=now,
    )
    proposal = RecoveryProposalEngine(get_node_contract).propose(
        diagnosis=diagnosis,
        plan=plan,
        ticket=parent,
        project_policy=RecoveryQuotaLimits(**limits),
        usage=RecoveryQuotaUsage(),
        changes=changes,
        created_at=now,
    )
    candidate = next(item for item in proposal.candidates if item.action == "SAFE_RETRY")
    if changes is None:
        assert candidate.executable
    store.add_observation(observation)
    store.add_goal_evaluation(evaluation)
    store.add_recovery_diagnosis(diagnosis)
    store.add_recovery_proposal(proposal)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id=lifecycle_id,
        project_id=project_id,
        state="RECOVERY_PROPOSED",
        reviewed_plan_id=reviewed_plan_id,
        execution_ticket_id=parent.execution_ticket_id,
        audit_id=parent.audit_id,
        run_id="parent-run",
        goal_contract_id=goal.goal_contract_id,
        goal_contract_hash=goal.goal_contract_hash,
        goal_evaluation_id=evaluation.goal_evaluation_id,
        goal_evaluation_summary=evaluation.summary(),
        diagnosis_id=diagnosis.diagnosis_id,
        diagnosis_summary=diagnosis.summary(),
        recovery_proposal_id=proposal.recovery_proposal_id,
        recovery_proposal_summary=proposal.summary(),
        observation_id=observation.observation_id,
        observation_summary=observation.summary(),
        created_at=now,
        updated_at=now,
        last_command_id="fixture",
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id=f"event-{uuid4().hex}",
            lifecycle_id=lifecycle_id,
            project_id=project_id,
            command_id="fixture",
            actor="fixture",
            source_command="fixture",
            occurred_at=now,
            from_state="DIAGNOSING",
            to_state="RECOVERY_PROPOSED",
            reviewed_plan_id=reviewed_plan_id,
            execution_ticket_id=parent.execution_ticket_id,
            audit_id=parent.audit_id,
            run_id="parent-run",
            observation_id=observation.observation_id,
            goal_contract_id=goal.goal_contract_id,
            goal_evaluation_id=evaluation.goal_evaluation_id,
            diagnosis_id=diagnosis.diagnosis_id,
            recovery_proposal_id=proposal.recovery_proposal_id,
        ),
    )
    return SimpleNamespace(
        store=store,
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        project_root=project_root,
        rawdata=rawdata,
        inputs=inputs,
        outputs=outputs,
        config_path=config_path,
        pipeline_path=pipeline_path,
        plan=plan,
        reviewed=reviewed,
        goal=goal,
        parent=parent,
        observation=observation,
        evaluation=evaluation,
        diagnosis=diagnosis,
        proposal=proposal,
        candidate=candidate,
        limits=limits,
    )
