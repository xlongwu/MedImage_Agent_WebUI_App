from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_lifecycle import (
    AgentLifecycleEvent,
    AgentLifecycleRecord,
    DecisionItem,
    PendingDecisionBatch,
)
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord
from src.backend.app.schemas.execution_ticket import ExecutionTicketEvent
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.agent_harness import AgentHarnessAttempt, AgentHarnessStep
from src.backend.app.schemas.observation import (
    ArtifactObservation,
    CapabilityObservation,
    NodeObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    PipelineObservation,
    ScientificObservation,
)
from src.backend.app.services.agent_task_read_model import AgentTaskReadModel

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _project(project_id: str = "project-1") -> ProjectDetail:
    return ProjectDetail(
        id=project_id,
        name="Research cohort",
        study_id="study-1",
        modality="rs-fMRI",
        created_date="2026-07-16",
        subjects_count=12,
        current_pipeline_id="pipeline-1",
        sequences=[],
        scans_count=12,
        total_size="1 GB",
        current_model_id="none",
    )


def _lifecycle(state: str = "WAITING_FOR_APPROVAL") -> AgentLifecycleRecord:
    return AgentLifecycleRecord(
        lifecycle_id="task-1",
        project_id="project-1",
        state=state,
        reviewed_plan_id="plan-1",
        execution_ticket_id="ticket-1",
        run_id="run-1",
        created_at=NOW,
        updated_at=NOW,
    )


def _plan() -> ReviewedPlanRecord:
    return ReviewedPlanRecord(
        reviewed_plan_id="plan-1",
        project_id="project-1",
        project_config_path="C:/private/project.yaml",
        plan_hash="plan-hash",
        created_at=NOW.isoformat(),
        updated_at=NOW.isoformat(),
        payload={
            "goal_contract": {"goal_contract_id": "goal-1", "goal_text": "Compute FC"},
            "approval_summary": {
                "summary_hash": "summary-hash",
                "execution_environment_snapshot_id": "environment-snapshot-1",
                "execution_environment_hash": "environment-hash",
                "goal": "Compute FC",
                "dataset_summary": "12 subjects",
                "execution_summary": "Native preprocessing and FC",
                "write_roots": ["project://project-1/outputs"],
                "rawdata_read_only": True,
                "external_tools": [],
                "limitations": [],
                "science_changes": [],
                "sections": [],
                "expires_at": None,
            },
        },
    )


class ReadOnlyStore:
    def __init__(self, lifecycle: AgentLifecycleRecord | None = None) -> None:
        self.project = _project()
        self.lifecycle = lifecycle or _lifecycle()
        self.plan = _plan()
        self.write_calls: list[str] = []

    def get_project(self, project_id: str):
        return self.project if project_id == self.project.id else None

    def list_agent_lifecycles(self, project_id: str):
        return [self.lifecycle] if project_id == self.project.id else []

    def get_agent_lifecycle(self, lifecycle_id: str):
        return self.lifecycle if lifecycle_id == self.lifecycle.lifecycle_id else None

    def get_reviewed_plan(self, reviewed_plan_id: str):
        return self.plan if reviewed_plan_id == self.plan.reviewed_plan_id else None

    def get_execution_ticket(self, execution_ticket_id: str):
        return None

    def get_run_link_by_run_id(self, project_id: str, run_id: str):
        return None

    def get_observation(self, observation_id: str):
        return None

    def get_goal_evaluation(self, goal_evaluation_id: str):
        return None

    def get_recovery_diagnosis(self, diagnosis_id: str):
        return None

    def get_recovery_proposal(self, proposal_id: str):
        return None

    def get_recovery_approval(self, approval_id: str):
        return None

    def get_recovery_attempt(self, attempt_id: str):
        return None

    def list_agent_lifecycle_events(self, lifecycle_id: str):
        return []

    def list_execution_ticket_events(self, execution_ticket_id: str):
        return []

    def list_observations(self, project_id: str, **kwargs):
        return []

    def list_goal_evaluations(self, project_id: str, **kwargs):
        return []

    def list_recovery_diagnoses(self, project_id: str, **kwargs):
        return []

    def list_recovery_proposals(self, project_id: str, **kwargs):
        return []

    def list_recovery_approvals(self, project_id: str, **kwargs):
        return []

    def list_recovery_attempts(self, project_id: str, **kwargs):
        return []

    def __getattr__(self, name: str):
        if name.startswith(("add_", "create_", "transition_", "update_", "reserve_")):

            def reject_write(*args, **kwargs):
                self.write_calls.append(name)
                raise AssertionError(f"read model attempted write: {name}")

            return reject_write
        raise AttributeError(name)


def test_projection_is_stable_portable_and_read_only() -> None:
    store = ReadOnlyStore()
    service = AgentTaskReadModel(store)

    first = service.get(project_id="project-1", task_id="task-1")
    second = service.get(project_id="project-1", task_id="task-1")

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.state == "waiting_for_user"
    assert first.next_action.type == "approve_execution"
    assert first.approval_summary is not None
    assert first.technical_details.plan_hash == "plan-hash"
    dumped = first.model_dump_json()
    assert "C:/private" not in dumped
    assert all(link.uri.startswith("project://project-1/") for link in first.evidence_links)


def test_projection_exposes_frozen_memory_consent_and_provenance_without_mutation() -> None:
    lifecycle = _lifecycle().model_copy(
        update={
            "command_context": {
                "memory_context": {
                    "context_hash": "memory-context-hash",
                    "retrieval_policy_version": "memory-retrieval-v1",
                    "evidence_refs": [{"memory_id": "memory-1"}],
                    "status": "partial",
                    "used_bytes": 512,
                    "omitted_count": 2,
                },
                "memory_consent": {
                    "available": True,
                    "generate_enabled": False,
                    "use_enabled": True,
                    "consent_epoch": 3,
                },
                "memory_warnings": ["MEMORY_SOURCE_STALE:memory-1"],
            }
        }
    )
    store = ReadOnlyStore(lifecycle)

    projected = AgentTaskReadModel(store).get(
        project_id="project-1", task_id="task-1"
    )

    assert projected.technical_details.memory_context_hash == "memory-context-hash"
    assert projected.technical_details.memory_status == "partial"
    assert projected.technical_details.memory_used_bytes == 512
    assert projected.technical_details.memory_omitted_count == 2
    assert projected.technical_details.memory_available is True
    assert projected.technical_details.memory_generate_enabled is False
    assert projected.technical_details.memory_use_enabled is True
    assert projected.technical_details.memory_warnings == (
        "MEMORY_SOURCE_STALE:memory-1",
    )
    assert store.write_calls == []
    assert store.write_calls == []


@pytest.mark.parametrize("state", ["GOAL_SATISFIED", "SUCCEEDED"])
def test_terminal_state_without_observation_and_evaluation_is_not_completed(state: str) -> None:
    store = ReadOnlyStore(_lifecycle(state))

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "needs_attention"
    assert projected.outcome == "indeterminate"
    assert projected.result_summary is None
    assert projected.next_action.type == "view_attention"


def test_human_handoff_projects_terminal_progress_phase() -> None:
    store = ReadOnlyStore(_lifecycle("HUMAN_HANDOFF"))

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "needs_attention"
    assert projected.progress.phase == "complete"


def _terminal_evidence(*, reload_status: str = "passed", completeness: str = "complete"):
    observation = ObservationRecord(
        observation_id="observation-1",
        bindings=ObservationBindings(
            project_id="project-1",
            lifecycle_id="task-1",
            reviewed_plan_id="plan-1",
            plan_hash="plan-hash",
            goal_contract_id="goal-1",
            goal_contract_hash="goal-hash",
            run_id="run-1",
            execution_ticket_id="ticket-1",
            dispatch_id="dispatch-1",
        ),
        collected_at=NOW,
        sources=(),
        pipeline=PipelineObservation(status="SUCCESS", summary_consistent=True),
        nodes=(NodeObservation(node_id="fc", subject_id="sub-01", status="SUCCESS"),),
        artifacts=(
            ArtifactObservation(
                artifact_id="artifact-1",
                artifact_type="functional_connectivity_matrix",
                relative_path="outputs/sub-01/fc.npy",
                exists=True,
                checksum_sha256="abc123",
                reload_status=reload_status,
                registration_status="registered",
            ),
        ),
        capability=CapabilityObservation(
            declared_level="validated",
            observed_level="validated",
            defensible_level="validated",
        ),
        scientific=ScientificObservation(status="validated"),
        completeness=ObservationCompleteness(status=completeness),
        observation_hash="observation-hash",
    )
    evaluation = GoalEvaluationRecord(
        goal_evaluation_id="evaluation-1",
        project_id="project-1",
        lifecycle_id="task-1",
        reviewed_plan_id="plan-1",
        plan_hash="plan-hash",
        goal_contract_id="goal-1",
        goal_contract_hash="goal-hash",
        observation_id="observation-1",
        observation_hash="observation-hash",
        evaluated_at=NOW,
        criterion_results=(),
        status="satisfied",
        goal_evaluation_hash="evaluation-hash",
    )
    return observation, evaluation


def test_completed_requires_satisfied_evaluation_and_reloadable_registered_artifact() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED").model_copy(
        update={"observation_id": "observation-1", "goal_evaluation_id": "evaluation-1"}
    )
    store = ReadOnlyStore(lifecycle)
    observation, evaluation = _terminal_evidence()
    store.get_observation = lambda observation_id: observation
    store.get_goal_evaluation = lambda evaluation_id: evaluation
    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "completed"
    assert projected.outcome == "succeeded"
    assert projected.result_summary is not None
    assert projected.result_summary.artifacts[0].uri == ("project://project-1/artifacts/artifact-1")
    assert projected.result_explanation is not None
    assert projected.result_explanation.outcome == "succeeded"
    assert projected.result_explanation.artifact_refs[0].artifact_id == "artifact-1"


def test_projection_uses_deterministic_result_explanation_without_harness_text() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED").model_copy(
        update={"observation_id": "observation-1", "goal_evaluation_id": "evaluation-1"}
    )
    store = ReadOnlyStore(lifecycle)
    observation, evaluation = _terminal_evidence()
    store.get_observation = lambda observation_id: observation
    store.get_goal_evaluation = lambda evaluation_id: evaluation
    store.get_agent_harness_attempt = lambda lifecycle_id: AgentHarnessAttempt(
        attempt_id="harness-1", lifecycle_id=lifecycle_id, project_id="project-1",
        provider_ref="rule_based", status="FINISHED", deadline_at=NOW,
    )
    store.list_agent_harness_steps = lambda attempt_id: []

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.result_explanation is not None
    assert projected.result_explanation.generated_text is None
    assert projected.result_explanation.generated_text_status == "not_requested"
    assert projected.harness_summary is not None
    assert projected.harness_summary.actual_provider == "rule_based"
    assert projected.harness_summary.steps_limit == 8
    assert projected.harness_summary.input_tokens_used is None
    assert projected.automation.level == "A4"
    assert projected.automation.reason == "task_terminal"


def test_reload_failure_and_partial_evidence_never_project_completed() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED").model_copy(
        update={"observation_id": "observation-1", "goal_evaluation_id": "evaluation-1"}
    )
    store = ReadOnlyStore(lifecycle)
    observation, evaluation = _terminal_evidence(reload_status="failed", completeness="partial")
    store.get_observation = lambda observation_id: observation
    store.get_goal_evaluation = lambda evaluation_id: evaluation

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "needs_attention"
    assert projected.outcome == "partial"
    assert projected.result_summary is not None
    assert projected.result_summary.artifacts[0].reload_status == "failed"


def test_event_pagination_is_stable_without_duplicates_and_cursor_is_bound() -> None:
    store = ReadOnlyStore()
    store.list_agent_lifecycle_events = lambda lifecycle_id: [
        AgentLifecycleEvent(
            event_id="event-2",
            lifecycle_id="task-1",
            project_id="project-1",
            command_id="command-2",
            actor="user",
            source_command="plan",
            occurred_at=NOW,
            from_state="CONTEXT_READY",
            to_state="PLAN_DRAFTED",
        ),
        AgentLifecycleEvent(
            event_id="event-1",
            lifecycle_id="task-1",
            project_id="project-1",
            command_id="command-1",
            actor="user",
            source_command="create",
            occurred_at=NOW,
            from_state=None,
            to_state="CREATED",
        ),
    ]
    store.list_execution_ticket_events = lambda ticket_id: [
        ExecutionTicketEvent(
            event_id="ticket-event-1",
            execution_ticket_id="ticket-1",
            project_id="project-1",
            event_type="issued",
            occurred_at=NOW,
        )
    ]
    service = AgentTaskReadModel(store)

    page_1 = service.events(project_id="project-1", task_id="task-1", limit=2)
    page_2 = service.events(
        project_id="project-1",
        task_id="task-1",
        after=page_1.next_cursor,
        limit=2,
    )

    ids = [item.event_id for item in [*page_1.items, *page_2.items]]
    assert ids == ["event-1", "event-2", "ticket-event-1"]
    assert len(ids) == len(set(ids))
    assert page_2.next_cursor is None

    with pytest.raises(SafetyError, match="AGENT_TASK_CURSOR_SCOPE_MISMATCH"):
        service.events(
            project_id="project-2",
            task_id="task-1",
            after=page_1.next_cursor,
            limit=2,
        )


def test_unknown_internal_state_fails_closed() -> None:
    store = ReadOnlyStore()
    payload = _lifecycle().model_dump()
    payload["state"] = "FUTURE_STATE"
    store.lifecycle = SimpleNamespace(**payload)

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "needs_attention"
    assert projected.technical_details.internal_state == "FUTURE_STATE"


def test_goal_revision_is_not_project_input_and_exposes_decision_batch_id() -> None:
    pending = PendingDecisionBatch(
        batch_id="decision-revise",
        lifecycle_id="task-1",
        project_id="project-1",
        evidence_snapshot_hash="evidence-revise",
        expires_at=NOW.replace(year=2027),
        items=(DecisionItem(
            item_id="goal_revision",
            kind="goal_revision",
            question="Revise the research goal.",
            impact="UNSUPPORTED_GOAL",
            answer_type="text",
        ),),
    )
    lifecycle = _lifecycle("WAITING_FOR_INPUT").model_copy(
        update={"pending_decision_batch": pending}
    )
    projected = AgentTaskReadModel(ReadOnlyStore(lifecycle)).get(
        project_id="project-1", task_id="task-1"
    )

    assert projected.next_action.type == "revise_goal"
    assert projected.next_action.decision_batch_id == "decision-revise"
    assert projected.decision_batch is not None
    assert projected.decision_batch.items[0].kind == "goal_revision"


def test_canceled_task_projects_as_terminal_without_attention() -> None:
    projected = AgentTaskReadModel(ReadOnlyStore(_lifecycle("CANCELED"))).get(
        project_id="project-1", task_id="task-1"
    )

    assert projected.state == "completed"
    assert projected.outcome == "canceled"
    assert projected.next_action.type == "none"


def test_plan_only_completion_has_truthful_metadata_result_without_run() -> None:
    lifecycle = _lifecycle("SUCCEEDED").model_copy(
        update={"execution_ticket_id": None, "run_id": None}
    )
    store = ReadOnlyStore(lifecycle)
    store.plan = store.plan.model_copy(
        update={
            "payload": {
                "goal": "仅生成静息态预处理方案，不执行计算",
                "plan": {
                    "metadata": {
                        "plan_only": True,
                        "capability_level": "metadata_only",
                        "execution_enabled": False,
                    }
                },
                "goal_contract": {
                    "goal_contract_id": "goal-plan-only",
                    "goal_text": "仅生成静息态预处理方案，不执行计算",
                },
                "execution_status": "NOT_EXECUTED_PLAN_ONLY",
            }
        }
    )

    projected = AgentTaskReadModel(store).get(project_id="project-1", task_id="task-1")

    assert projected.state == "completed"
    assert projected.outcome == "succeeded"
    assert projected.result_summary is not None
    assert projected.result_summary.artifacts[0].artifact_type == "reviewed_plan"
    assert projected.result_summary.artifacts[0].capability_level == "metadata_only"
    assert projected.next_action.type == "review_results"
    assert projected.next_action.requires_user is False
    assert projected.progress.phase == "complete"
    assert projected.technical_details.ticket_id is None
    assert projected.technical_details.run_id is None
