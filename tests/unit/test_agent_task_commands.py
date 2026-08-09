from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_lifecycle import (
    AgentLifecycleEvent,
    AgentLifecycleRecord,
    DecisionItem,
    PendingDecisionBatch,
    PendingDecisionOption,
)
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_task_command_service import (
    AgentTaskCommandService,
    execution_prerequisite_issue,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService
from tests.helpers_phase8 import build_recovery_fixture
from tests.unit.test_agent_task_read_model import ReadOnlyStore

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def test_reho_approval_preflight_rejects_missing_preprocessed_input() -> None:
    broken_plan = {
        "nodes": [
            {"id": "data_inspection", "params": {}},
            {
                "id": "nuisance_regression_subject",
                "depends_on": ["data_inspection"],
                "params": {},
            },
            {
                "id": "reho_subject",
                "depends_on": ["nuisance_regression_subject"],
                "params": {},
            },
        ]
    }

    issue = execution_prerequisite_issue(broken_plan)

    assert issue is not None
    assert "realignment" in issue


def test_reho_approval_preflight_accepts_native_realignment_chain() -> None:
    safe_plan = {
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "params": {
                    "input_bids_dir": "C:/research/demo/rawdata",
                    "stage_overrides": {"realignment": True, "reho": True},
                },
            }
        ]
    }

    assert execution_prerequisite_issue(safe_plan) is None


def test_subject_science_decision_applies_reviewed_native_subject_scope() -> None:
    plan = {
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "params": {"input_bids_dir": "C:/research/demo/rawdata"},
            }
        ],
        "metadata": {
            "science_decisions": {
                "subject_selection_required": True,
                "subject_candidates": ["sub-001", "sub-002"],
            }
        },
    }

    decision = AgentTaskCommandService._science_decision_items(plan, {})[0]

    assert decision is not None
    assert decision.kind == "subject_id"
    assert [option.id for option in decision.options] == ["sub-001", "sub-002"]

    applied = AgentTaskCommandService._apply_science_answers(
        plan,
        {"science_answers": {"subject_id": "sub-001"}},
    )
    assert applied["nodes"][0]["params"]["subject_id"] == "sub-001"


def test_reho_approval_preflight_rejects_incomplete_native_preprocessing_chain() -> None:
    unsafe_plan = {
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "params": {
                    "input_bids_dir": "C:/research/demo/rawdata",
                    "stage_overrides": {
                        "realignment": True,
                        "motion_qc": True,
                        "nuisance_regression": True,
                        "detrending": True,
                        "temporal_filtering": False,
                        "reho": True,
                    },
                },
            }
        ]
    }

    issue = execution_prerequisite_issue(unsafe_plan)

    assert issue is not None
    assert "temporal_filtering" in issue


def test_current_lifecycle_payload_uses_batch_decision_defaults() -> None:
    record = AgentLifecycleRecord.model_validate(
        {
            "schema_version": 5,
            "lifecycle_id": "current-5",
            "project_id": "project-1",
            "state": "CREATED",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    assert record.goal_text is None
    assert record.goal_hash is None
    assert record.pending_decision_batch is None
    assert record.canceled_at is None


def test_waiting_input_and_science_decision_are_resumable_and_cancelable() -> None:
    store = ReadOnlyStore()
    orchestrator = AgentOrchestrator(store)
    lifecycle = store.lifecycle

    # Reuse the fake store as a deterministic in-memory transition ledger.
    store.project = store.project
    store.lifecycle = lifecycle.model_copy(update={"state": "CREATED"})
    store.transition_agent_lifecycle = lambda record, event, expected_state: (
        setattr(store, "lifecycle", record) or record
    )

    missing = PendingDecisionBatch(
        batch_id="decision-input",
        lifecycle_id="task-1",
        project_id="project-1",
        evidence_snapshot_hash="evidence-input",
        expires_at=NOW.replace(year=2027),
        items=(DecisionItem(
            item_id="missing_input",
            kind="missing_input",
            question="Select a registered dataset.",
            impact="Planning cannot continue without project data.",
            answer_type="text",
        ),),
    )
    waiting = orchestrator.transition(
        project_id="project-1",
        lifecycle_id="task-1",
        to_state="WAITING_FOR_INPUT",
        command_id="wait-input",
        actor="user",
        source_command="missing_input",
        updates={"pending_decision_batch": missing},
    )
    assert waiting.pending_decision_batch == missing

    resumed = orchestrator.transition(
        project_id="project-1",
        lifecycle_id="task-1",
        to_state="CONTEXT_READY",
        command_id="answer-input",
        actor="user",
        source_command="input_answered",
        updates={"pending_decision_batch": None},
    )
    drafted = orchestrator.transition(
        project_id="project-1",
        lifecycle_id="task-1",
        to_state="PLAN_DRAFTED",
        command_id="draft",
        actor="planner",
        source_command="plan_drafted",
    )
    science = PendingDecisionBatch(
        batch_id="decision-atlas",
        lifecycle_id="task-1",
        project_id="project-1",
        evidence_snapshot_hash="evidence-atlas",
        plan_hash_before="plan-hash-before",
        expires_at=NOW.replace(year=2027),
        items=(DecisionItem(
            item_id="atlas",
            kind="atlas",
            question="Which atlas should define FC regions?",
            options=(
            PendingDecisionOption(
                id="schaefer-200",
                label="Schaefer 200",
                description="A 200-region cortical parcellation.",
                recommended=True,
            ),
            ),
            recommended_option="schaefer-200",
            impact="Changing the atlas changes matrix dimensions and comparability.",
        ),),
    )
    waiting_science = orchestrator.transition(
        project_id="project-1",
        lifecycle_id="task-1",
        to_state="WAITING_FOR_SCIENCE_DECISION",
        command_id="wait-science",
        actor="planner",
        source_command="science_decision_required",
        updates={"pending_decision_batch": science},
    )
    assert waiting_science.pending_decision_batch.items[0].kind == "atlas"

    canceled = orchestrator.cancel(
        project_id="project-1",
        lifecycle_id="task-1",
        command_id="cancel",
        actor="user",
        reason="User stopped before dispatch",
    )
    assert canceled.state == "CANCELED"
    assert canceled.pending_decision_batch is None
    assert canceled.canceled_by == "user"
    assert resumed.state == "CONTEXT_READY"
    assert drafted.state == "PLAN_DRAFTED"


def test_running_task_cannot_be_canceled() -> None:
    store = ReadOnlyStore()
    store.lifecycle = store.lifecycle.model_copy(update={"state": "RUNNING"})

    with pytest.raises(SafetyError, match="LIFECYCLE_CANCEL_NOT_SUPPORTED"):
        AgentOrchestrator(store).cancel(
            project_id="project-1",
            lifecycle_id="task-1",
            command_id="cancel-running",
            actor="user",
        )


def test_canceled_task_is_idempotent_across_new_cancel_commands() -> None:
    store = ReadOnlyStore()
    store.lifecycle = store.lifecycle.model_copy(
        update={"state": "CANCELED", "last_command_id": "cancel-original"}
    )

    canceled = AgentOrchestrator(store).cancel(
        project_id="project-1",
        lifecycle_id="task-1",
        command_id="cancel-retry",
        actor="user",
    )

    assert canceled.state == "CANCELED"
    assert canceled.last_command_id == "cancel-original"


class CommandStore:
    def __init__(self, tmp_path) -> None:
        self.project = ProjectDetail(
            id="project-1",
            name="Study",
            study_id="study-1",
            modality="rs-fMRI",
            created_date="2026-07-16",
            subjects_count=2,
            current_pipeline_id="",
            sequences=[],
            scans_count=2,
            total_size="1 MB",
            current_model_id="",
            metadata={
                "project_dir": str(tmp_path),
                "project_config_path": str(tmp_path / "project.yaml"),
                "agent_planner_provider": "rule_based",
            },
        )
        self.lifecycles: dict[str, AgentLifecycleRecord] = {}
        self.events: dict[str, list[AgentLifecycleEvent]] = {}
        self.plans: dict[str, ReviewedPlanRecord] = {}

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None

    def create_agent_lifecycle(self, record, event):
        self.lifecycles[record.lifecycle_id] = record
        self.events[record.lifecycle_id] = [event]
        return record

    def get_agent_lifecycle(self, lifecycle_id):
        return self.lifecycles.get(lifecycle_id)

    def list_agent_lifecycles(self, project_id):
        return [record for record in self.lifecycles.values() if record.project_id == project_id]

    def transition_agent_lifecycle(self, record, event, *, expected_state):
        assert self.lifecycles[record.lifecycle_id].state == expected_state
        self.lifecycles[record.lifecycle_id] = record
        self.events[record.lifecycle_id].append(event)
        return record

    def list_agent_lifecycle_events(self, lifecycle_id):
        return list(self.events.get(lifecycle_id, []))

    def get_reviewed_plan(self, reviewed_plan_id):
        return self.plans.get(reviewed_plan_id)

    def update_reviewed_plan(self, reviewed_plan_id, **updates):
        current = self.plans.get(reviewed_plan_id)
        if current is None:
            return None
        updated = current.model_copy(update=updates)
        self.plans[reviewed_plan_id] = updated
        return updated

    def get_execution_ticket(self, _execution_ticket_id):
        return None

    def get_run_link_by_run_id(self, _project_id, _run_id):
        return None


def _planner(*, request, **_kwargs):
    return {
        "ok": True,
        "plan": {
            "pipeline_id": "fc-plan",
            "schema_version": "1.0",
            "nodes": [
                {
                    "id": "functional_connectivity_subject",
                    "backend": "python-cpu",
                    "params": {"atlas": "schaefer-200", "output_dir": "derivatives/fc"},
                }
            ],
        },
        "validation": {"ok": True},
        "warnings": [],
    }


def _missing_atlas_planner(**kwargs):
    result = _planner(**kwargs)
    result["plan"]["nodes"][0]["params"].pop("atlas")
    return result


def _plan_saver(store):
    def save(**kwargs):
        record = ReviewedPlanRecord(
            reviewed_plan_id="reviewed-1",
            project_id=kwargs["project_id"],
            project_config_path=kwargs["project_config_path"],
            plan_hash="plan-hash",
            created_at=NOW.isoformat(),
            updated_at=NOW.isoformat(),
            payload={
                "plan": kwargs["plan"],
                "goal": kwargs["goal"],
                "goal_contract": {
                    "goal_contract_id": "contract-1",
                    "goal_contract_hash": "contract-hash",
                    "goal_text": kwargs["goal"],
                },
            },
        )
        store.plans[record.reviewed_plan_id] = record
        return record

    return save


def test_memory_scientific_suggestion_requires_current_task_confirmation(
    tmp_path,
) -> None:
    from src.backend.app.planner.audit_record import stable_hash
    from src.backend.app.schemas.memory import MemoryContext, MemoryDecisionSuggestion

    store = CommandStore(tmp_path)
    suggestion = MemoryDecisionSuggestion(
        memory_id="memory-atlas-1",
        revision_hash="revision-hash",
        decision_kind="atlas",
        typed_value={"decision_kind": "atlas", "value": "schaefer-200"},
        algorithm_id="confirmed-project-decision",
        algorithm_version="1",
        config_fingerprint="config-hash",
        applicability={"project_id": "project-1"},
        confirmation_event_id="event-1",
        source_refs=("agent_lifecycle_event:event-1",),
    )
    identity = {
        "schema_version": "memory-context-v1",
        "retrieval_policy_version": "memory-retrieval-v1",
        "project_id": "project-1",
        "planner_constraints": {},
        "decision_suggestions": [suggestion.model_dump(mode="json")],
        "evidence_refs": [],
        "omitted_count": 0,
        "used_bytes": 0,
        "status": "enabled",
        "warning_codes": [],
    }
    context = MemoryContext(**identity, context_hash=stable_hash(identity))

    class ContextService:
        def build_context_with_warnings(self, **_kwargs):
            return context, ()

    def planner(**_kwargs):
        return {
            "ok": True,
            "plan": {
                "pipeline_id": "native-plan",
                "schema_version": "1.0",
                "nodes": [
                    {
                        "id": "native_preproc_full_execute",
                        "backend": "python-cpu",
                        "params": {
                            "project_id": "project-1",
                            "project_dir": str(tmp_path),
                            "atlas": "schaefer-200",
                            "output_dir": "derivatives/native",
                            "confirmations": {},
                        },
                    }
                ],
            },
            "validation": {"ok": True},
            "warnings": [],
        }

    service = AgentTaskCommandService(
        store,
        planner=planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
        memory_context_service=ContextService(),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Run native preprocessing",
        command_id="memory-create-0001",
        actor="user",
    )
    assert waiting.state == "WAITING_FOR_SCIENCE_DECISION"
    assert waiting.pending_decision_batch.source == "memory_suggestion"
    assert waiting.pending_decision_batch.items[0].memory_id == "memory-atlas-1"

    resumed = service.answer(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        batch_id=waiting.pending_decision_batch.batch_id,
        answers=[{"item_id": "memory_atlas_memory-atlas-1", "value": "schaefer-200"}],
        command_id="memory-answer-0001",
        actor="user",
    )
    assert resumed.state == "WAITING_FOR_APPROVAL"
    assert resumed.command_context["science_answers"]["atlas"] == "schaefer-200"
    assert resumed.command_context["memory_context"]["context_hash"] == context.context_hash


def _service(tmp_path, *, planner=_planner, executor=None, conversion_checker=None):
    store = CommandStore(tmp_path)
    service = AgentTaskCommandService(
        store,
        planner=planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
        executor=executor,
        conversion_checker=conversion_checker,
    )
    return store, service


def test_create_is_bounded_and_stops_at_single_approval(tmp_path) -> None:
    store, service = _service(tmp_path)
    lifecycle = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-1",
        actor="user",
    )
    assert lifecycle.state == "WAITING_FOR_APPROVAL"
    assert lifecycle.reviewed_plan_id == "reviewed-1"
    assert len(store.events[lifecycle.lifecycle_id]) == 5
    plan = store.get_reviewed_plan("reviewed-1")
    assert plan.payload["approval_summary"]["summary_hash"]
    assert plan.payload["approval_envelope"]["confirmations"]["approved_nodes"] == [
        "functional_connectivity_subject"
    ]


def test_planning_never_invokes_execution_dry_run_before_user_approval(tmp_path) -> None:
    store = CommandStore(tmp_path)
    dry_run_calls = []
    service = AgentTaskCommandService(
        store,
        planner=_planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **kwargs: dry_run_calls.append(kwargs)
        or {"ok": False, "status": "APPROVAL_GATE_BLOCKED"},
    )

    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-no-preapproval-dry-run",
        actor="user",
    )

    assert waiting.state == "WAITING_FOR_APPROVAL"
    assert waiting.pending_decision_batch is None
    assert dry_run_calls == []
    reviewed = store.get_reviewed_plan("reviewed-1")
    assert reviewed.payload["dry_run"]["status"] == "PENDING_USER_APPROVAL"


def test_science_decision_blocks_approval_and_answer_rebuilds_plan(tmp_path) -> None:
    store, service = _service(tmp_path, planner=_missing_atlas_planner)
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-2",
        actor="user",
    )
    assert waiting.state == "WAITING_FOR_SCIENCE_DECISION"
    assert store.plans == {}

    ready = service.answer(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        batch_id=waiting.pending_decision_batch.batch_id,
        answers=[{"item_id": "atlas", "value": "schaefer-200"}],
        command_id="answer-2",
        actor="user",
    )
    assert ready.state == "WAITING_FOR_APPROVAL"
    assert (
        store.get_reviewed_plan("reviewed-1").payload["plan"]["nodes"][0]["params"]["atlas"]
        == "schaefer-200"
    )


def test_subject_decision_rebuilds_reviewed_plan_and_approval_scope(tmp_path) -> None:
    def planner(**_kwargs):
        return {
            "ok": True,
            "plan": {
                "pipeline_id": "native_full_preprocessing",
                "schema_version": "1.0",
                "nodes": [
                    {
                        "id": "native_preproc_full_execute",
                        "backend": "native_python",
                        "params": {
                            "input_bids_dir": "C:/research/demo/rawdata",
                            "confirmations": {},
                        },
                    }
                ],
                "metadata": {
                    "science_decisions": {
                        "subject_selection_required": True,
                        "subject_candidates": ["sub-001", "sub-002"],
                    }
                },
            },
            "validation": {"ok": True},
            "warnings": [],
        }

    store, service = _service(tmp_path, planner=planner)
    waiting = service.create(
        project_id="project-1",
        goal="Select one registered subject for native preprocessing",
        command_id="create-subject-scope",
        actor="user",
    )

    assert waiting.state == "WAITING_FOR_SCIENCE_DECISION"
    assert waiting.pending_decision_batch.items[0].kind == "subject_id"
    assert store.plans == {}

    ready = service.answer(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        batch_id=waiting.pending_decision_batch.batch_id,
        answers=[{"item_id": "subject_id", "value": "sub-001"}],
        command_id="answer-subject-scope",
        actor="user",
    )

    assert ready.state == "WAITING_FOR_APPROVAL"
    reviewed = store.get_reviewed_plan("reviewed-1")
    node = reviewed.payload["plan"]["nodes"][0]
    summary = reviewed.payload["approval_summary"]
    assert node["params"]["subject_id"] == "sub-001"
    assert summary["dataset_summary"] == "1 selected subject: sub-001"
    assert summary["sections"][0]["summary"] == (
        "Approve exactly 1 reviewed node(s) for subject sub-001."
    )


@pytest.mark.parametrize(
    ("signals", "backend", "kind", "answer", "expected_key", "expected_value"),
    [
        (
            {"global_signal_regression_required": True},
            "python-cpu",
            "global_signal_regression",
            "include",
            "global_signal_regression",
            True,
        ),
        (
            {"tr_conflict": {"bids": 2.0, "project": 2.2}},
            "python-cpu",
            "repetition_time",
            "bids",
            "tr",
            2.0,
        ),
        (
            {"template_required": True},
            "python-cpu",
            "template",
            "MNI152NLin6Asym",
            "template",
            "MNI152NLin6Asym",
        ),
        (
            {"existing_run_conflict": True},
            "python-cpu",
            "overwrite",
            "write_new_run_directory",
            "overwrite_policy",
            "write_new_run_directory",
        ),
        (
            {"experimental_gpu": True, "cpu_backend": "python-cpu"},
            "gpu-experimental",
            "experimental_backend",
            "use_cpu",
            "backend",
            "python-cpu",
        ),
    ],
)
def test_science_decision_matrix_requires_explicit_answer_and_rebuilds_plan(
    tmp_path, signals, backend, kind, answer, expected_key, expected_value
) -> None:
    def planner(**kwargs):
        result = _planner(**kwargs)
        result["plan"]["nodes"][0]["backend"] = backend
        result["plan"]["metadata"] = {"science_decisions": signals}
        return result

    store, service = _service(tmp_path, planner=planner)
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id=f"create-{kind}",
        actor="user",
    )
    assert waiting.state == "WAITING_FOR_SCIENCE_DECISION"
    assert waiting.pending_decision_batch.items[0].kind == kind
    assert store.plans == {}

    ready = service.answer(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        batch_id=waiting.pending_decision_batch.batch_id,
        answers=[{"item_id": kind, "value": answer}],
        command_id=f"answer-{kind}",
        actor="user",
    )
    assert ready.state == "WAITING_FOR_APPROVAL"
    node = store.get_reviewed_plan("reviewed-1").payload["plan"]["nodes"][0]
    if expected_key == "backend":
        assert node["backend"] == expected_value
    else:
        assert node["params"][expected_key] == expected_value
    answer_event = next(
        event for event in store.events[ready.lifecycle_id] if event.source_command == "answer"
    )
    assert answer_event.details == {
        "batch_id": waiting.pending_decision_batch.batch_id,
        "item_ids": [kind],
    }


def test_tampered_approval_hash_fails_before_shared_execution(tmp_path) -> None:
    calls = []
    executor = ReviewedExecutionService(
        executor=lambda request: calls.append(request) or {"ok": True}
    )
    store, service = _service(tmp_path, executor=executor)
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-3",
        actor="user",
    )
    with pytest.raises(SafetyError, match="APPROVAL_SUMMARY_STALE"):
        service.approve(
            project_id="project-1",
            lifecycle_id=waiting.lifecycle_id,
            approval_summary_hash="tampered",
            command_id="approve-3",
            actor="user",
        )
    assert calls == []


def test_valid_approval_runs_bound_dry_run_before_real_execution(tmp_path) -> None:
    store = CommandStore(tmp_path)
    calls = []

    def dry_run(**kwargs):
        calls.append(("dry_run", kwargs))
        return {"ok": True, "status": "DRY_RUN_OK"}

    def execute(request):
        calls.append(("execute", request))
        return {"ok": True, "status": "RUNNING"}

    service = AgentTaskCommandService(
        store,
        planner=_planner,
        plan_saver=_plan_saver(store),
        dry_runner=dry_run,
        executor=ReviewedExecutionService(executor=execute),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-bound-dry-run",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"][
        "summary_hash"
    ]

    service.approve(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        approval_summary_hash=summary_hash,
        command_id="approve-bound-dry-run",
        actor="user",
    )

    assert [kind for kind, _ in calls] == ["dry_run", "execute"]
    dry_run_args = calls[0][1]
    assert dry_run_args["reviewed_plan_id"] == "reviewed-1"
    assert dry_run_args["approval"]["approval_summary_hash"] == summary_hash


def test_failed_post_approval_dry_run_prevents_real_execution(tmp_path) -> None:
    store = CommandStore(tmp_path)
    execute_calls = []
    service = AgentTaskCommandService(
        store,
        planner=_planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **_kwargs: {
            "ok": False,
            "status": "EXECUTION_POLICY_BLOCKED",
        },
        executor=ReviewedExecutionService(
            executor=lambda request: execute_calls.append(request) or {"ok": True}
        ),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-blocked-post-approval-dry-run",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"][
        "summary_hash"
    ]

    with pytest.raises(SafetyError, match="AGENT_DRY_RUN_BLOCKED") as exc_info:
        service.approve(
            project_id="project-1",
            lifecycle_id=waiting.lifecycle_id,
            approval_summary_hash=summary_hash,
            command_id="approve-blocked-post-approval-dry-run",
            actor="user",
        )

    assert execute_calls == []
    assert exc_info.value.details == {"blocked_status": "EXECUTION_POLICY_BLOCKED"}


def test_reviewed_execution_disabled_preserves_structured_block_reason(tmp_path) -> None:
    store, service = _service(
        tmp_path,
        executor=ReviewedExecutionService(
            executor=lambda _request: {
                "ok": False,
                "status": "REVIEWED_EXECUTION_DISABLED",
            }
        ),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-disabled-execution",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"][
        "summary_hash"
    ]

    with pytest.raises(SafetyError) as caught:
        service.approve(
            project_id="project-1",
            lifecycle_id=waiting.lifecycle_id,
            approval_summary_hash=summary_hash,
            command_id="approve-disabled-execution",
            actor="user",
        )

    assert caught.value.code == "AGENT_EXECUTION_BLOCKED"
    assert caught.value.details == {
        "blocked_status": "REVIEWED_EXECUTION_DISABLED",
        "required_environment": ["MEDIMAGE_ENABLE_REVIEWED_EXECUTION"],
        "retryable_after_configuration": True,
    }
    assert store.get_agent_lifecycle(waiting.lifecycle_id).state == "WAITING_FOR_APPROVAL"


def test_reho_approval_blocks_legacy_plan_without_preprocessed_input(tmp_path) -> None:
    def broken_reho_planner(**_kwargs):
        return {
            "ok": True,
            "plan": {
                "pipeline_id": "legacy-reho",
                "nodes": [
                    {"id": "data_inspection", "backend": "python", "params": {}},
                    {
                        "id": "nuisance_regression_subject",
                        "backend": "python",
                        "depends_on": ["data_inspection"],
                        "params": {},
                    },
                    {
                        "id": "reho_subject",
                        "backend": "python",
                        "depends_on": ["nuisance_regression_subject"],
                        "params": {},
                    },
                    {
                        "id": "reho_qc_dataset_report",
                        "backend": "python",
                        "depends_on": ["reho_subject"],
                        "params": {},
                    },
                ],
            },
            "validation": {"ok": True},
            "warnings": [],
        }

    execute_calls = []
    store, service = _service(
        tmp_path,
        planner=broken_reho_planner,
        executor=ReviewedExecutionService(
            executor=lambda request: execute_calls.append(request) or {"ok": True}
        ),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Compute ReHo",
        command_id="create-broken-reho",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"][
        "summary_hash"
    ]

    with pytest.raises(SafetyError, match="AGENT_EXECUTION_PREREQUISITE_MISSING"):
        service.approve(
            project_id="project-1",
            lifecycle_id=waiting.lifecycle_id,
            approval_summary_hash=summary_hash,
            command_id="approve-broken-reho",
            actor="user",
        )

    assert execute_calls == []


def test_valid_approval_dispatches_once_and_schedules_bounded_reconcile(tmp_path) -> None:
    calls = []
    scheduled = []
    holder = {}

    def execute(request):
        calls.append(request)
        store = holder["store"]
        current = store.get_agent_lifecycle(request.lifecycle_id)
        store.lifecycles[request.lifecycle_id] = current.model_copy(
            update={
                "state": "RUNNING",
                "run_id": "run-approval-1",
                "execution_ticket_id": "ticket-approval-1",
            }
        )
        return {"ok": True, "status": "RUNNING"}

    store, service = _service(
        tmp_path,
        executor=ReviewedExecutionService(executor=execute),
    )
    holder["store"] = store
    service.monitor_scheduler = lambda **kwargs: scheduled.append(kwargs) or True
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-valid-approval",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"]["summary_hash"]

    running = service.approve(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        approval_summary_hash=summary_hash,
        command_id="approve-valid",
        actor="user",
    )

    assert running.state == "RUNNING"
    assert len(calls) == 1
    assert scheduled == [{"project_id": "project-1", "lifecycle_id": waiting.lifecycle_id}]


def test_approval_reconciles_terminal_run_before_returning_to_frontend(tmp_path) -> None:
    holder = {}
    scheduled = []

    def execute(request):
        store = holder["store"]
        current = store.get_agent_lifecycle(request.lifecycle_id)
        store.lifecycles[request.lifecycle_id] = current.model_copy(
            update={
                "state": "RUNNING",
                "run_id": "run-terminal-1",
                "execution_ticket_id": "ticket-terminal-1",
            }
        )
        return {"ok": True, "status": "PARTIAL"}

    def reconcile_once(*, project_id: str, lifecycle_id: str):
        store = holder["store"]
        current = store.get_agent_lifecycle(lifecycle_id)
        terminal = current.model_copy(update={"state": "HUMAN_HANDOFF"})
        store.lifecycles[lifecycle_id] = terminal
        return terminal

    store = CommandStore(tmp_path)
    holder["store"] = store
    service = AgentTaskCommandService(
        store,
        planner=_planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
        executor=ReviewedExecutionService(executor=execute),
        reconcile_once=reconcile_once,
        monitor_scheduler=lambda **kwargs: scheduled.append(kwargs) or True,
    )
    waiting = service.create(
        project_id="project-1",
        goal="Compute functional connectivity",
        command_id="create-terminal-approval",
        actor="user",
    )
    summary_hash = store.get_reviewed_plan("reviewed-1").payload["approval_summary"][
        "summary_hash"
    ]

    terminal = service.approve(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        approval_summary_hash=summary_hash,
        command_id="approve-terminal",
        actor="user",
    )

    assert terminal.state == "HUMAN_HANDOFF"
    assert scheduled == []


def test_conversion_readiness_failure_stops_before_reviewed_plan(tmp_path) -> None:
    def conversion_planner(**_kwargs):
        return {
            "ok": True,
            "plan": {
                "pipeline_id": "dicom-plan",
                "nodes": [
                    {
                        "id": "native_dicom_conversion_execute",
                        "backend": "medimage-native",
                        "params": {
                            "project_id": "project-1",
                            "project_dir": str(tmp_path),
                            "rawdata_dir": str(tmp_path / "rawdata"),
                            "conversion_run_id": "conv-1",
                            "output_dir": str(tmp_path / "converted_bids"),
                        },
                    }
                ],
            },
            "validation": {"ok": True},
            "warnings": [],
        }

    calls = []
    store, service = _service(
        tmp_path,
        planner=conversion_planner,
        conversion_checker=lambda **kwargs: (
            calls.append(kwargs)
            or {"ok": False, "blocking_issues": ["CONVERSION_RELEASE_APPROVAL_REQUIRED"]}
        ),
    )
    waiting = service.create(
        project_id="project-1",
        goal="Convert DICOM",
        command_id="create-conversion-blocked",
        actor="user",
    )

    assert waiting.state == "WAITING_FOR_INPUT"
    assert waiting.pending_decision_batch.items[0].kind == "missing_input"
    assert "CONVERSION_RELEASE_APPROVAL_REQUIRED" in waiting.pending_decision_batch.items[0].impact
    assert store.plans == {}
    assert calls[0]["conversion_run_id"] == "conv-1"


def test_unsupported_goal_requests_goal_revision_and_answer_replaces_goal(tmp_path) -> None:
    def planner(*, request, **kwargs):
        if request.goal == "unsupported wording":
            return {
                "ok": False,
                "plan": {},
                "validation": {},
                "errors": ["UNSUPPORTED_GOAL: no matching pipeline"],
            }
        return _planner(request=request, **kwargs)

    store, service = _service(tmp_path, planner=planner)
    waiting = service.create(
        project_id="project-1",
        goal="unsupported wording",
        command_id="create-revision",
        actor="user",
    )

    assert waiting.state == "WAITING_FOR_INPUT"
    assert waiting.pending_decision_batch.items[0].kind == "goal_revision"

    ready = service.answer(
        project_id="project-1",
        lifecycle_id=waiting.lifecycle_id,
        batch_id=waiting.pending_decision_batch.batch_id,
        answers=[{"item_id": "goal_revision", "value": "Compute functional connectivity"}],
        command_id="answer-revision",
        actor="user",
    )

    assert ready.state == "WAITING_FOR_APPROVAL"
    assert ready.goal_text == "Compute functional connectivity"
    assert ready.goal_hash == stable_hash({"goal": "Compute functional connectivity"})


def test_plan_only_task_stores_reviewed_plan_and_finishes_without_dry_run_or_approval(
    tmp_path,
) -> None:
    store = CommandStore(tmp_path)
    dry_run_calls = []
    summary_calls = []

    def planner(**_kwargs):
        return {
            "ok": True,
            "plan": {
                "pipeline_id": "rsfmri_preproc_mvp",
                "nodes": [
                    {
                        "id": "data_readiness_check",
                        "backend": "python",
                        "params": {},
                    }
                ],
                "metadata": {
                    "plan_only": True,
                    "capability_level": "metadata_only",
                    "execution_enabled": False,
                    "rawdata_read_only": True,
                },
            },
            "validation": {"ok": True},
            "warnings": [],
        }

    class SummaryService:
        def build(self, **kwargs):
            summary_calls.append(kwargs)
            raise AssertionError("plan-only tasks must not create execution approval summaries")

    service = AgentTaskCommandService(
        store,
        planner=planner,
        plan_saver=_plan_saver(store),
        dry_runner=lambda **kwargs: dry_run_calls.append(kwargs) or {"ok": True},
        summary_service=SummaryService(),
    )

    completed = service.create(
        project_id="project-1",
        goal="仅生成静息态预处理方案，不执行计算",
        command_id="create-plan-only",
        actor="user",
    )

    assert completed.state == "SUCCEEDED"
    assert completed.reviewed_plan_id == "reviewed-1"
    assert completed.execution_ticket_id is None
    assert completed.run_id is None
    assert dry_run_calls == []
    assert summary_calls == []
    reviewed = store.get_reviewed_plan("reviewed-1")
    assert reviewed.payload["plan"]["metadata"]["plan_only"] is True
    assert reviewed.payload["execution_status"] == "NOT_EXECUTED_PLAN_ONLY"


def test_plan_only_task_passes_real_reviewed_plan_contract_validation(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "plan-only.sqlite")
    config_path = Path("examples/project_config_synthetic_smoke.yaml").resolve()
    store.add_project(
        ProjectDetail(
            id="project-plan-only",
            name="Plan only",
            study_id="study-plan-only",
            modality="rs-fMRI",
            created_date="2026-07-18",
            subjects_count=0,
            current_pipeline_id="",
            sequences=[],
            scans_count=0,
            total_size="0 MB",
            current_model_id="",
            metadata={
                "project_dir": str(tmp_path),
                "project_config_path": str(config_path),
                "agent_planner_provider": "rule_based",
            },
        ),
        health_status="Ready",
        rawdata_dir="",
    )

    completed = AgentTaskCommandService(store).create(
        project_id="project-plan-only",
        goal="检查已登记的 BIDS 数据并准备预处理方案，不执行任何计算，不修改 rawdata。",
        command_id="create-plan-only-real-store",
        actor="test",
    )

    assert completed.state == "SUCCEEDED"
    reviewed = store.get_reviewed_plan(completed.reviewed_plan_id)
    assert reviewed is not None
    assert reviewed.payload["goal_contract"]["goal_kind"] == "rsfmri_preprocessing_plan"
    assert reviewed.payload["execution_status"] == "NOT_EXECUTED_PLAN_ONLY"


def test_recovery_command_uses_recommended_candidate_and_one_explicit_approval(
    tmp_path, monkeypatch
) -> None:
    fixture = build_recovery_fixture(tmp_path)
    calls = []

    class FakeRecoveryExecutionService:
        def __init__(self, store):
            assert store is fixture.store

        def approve(self, **kwargs):
            calls.append(("approve", kwargs))

        def execute(self, **kwargs):
            calls.append(("execute", kwargs))
            lifecycle = fixture.store.get_agent_lifecycle(fixture.lifecycle_id)
            return (
                lifecycle.model_copy(update={"state": "GOAL_SATISFIED"}),
                object(),
                {"status": "SUCCESS"},
            )

    monkeypatch.setattr(
        "src.backend.app.services.agent_task_command_service.RecoveryExecutionService",
        FakeRecoveryExecutionService,
    )
    result = AgentTaskCommandService(fixture.store).approve_recovery(
        project_id=fixture.project_id,
        lifecycle_id=fixture.lifecycle_id,
        command_id="agent-recovery-approve",
        actor="local-user",
    )

    assert result.state == "GOAL_SATISFIED"
    assert [kind for kind, _ in calls] == ["approve", "execute"]
    assert calls[0][1]["candidate_id"] == fixture.proposal.recommended_candidate_id
    assert calls[1][1]["candidate_id"] == fixture.proposal.recommended_candidate_id
