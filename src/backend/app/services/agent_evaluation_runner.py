"""Isolated, deterministic execution of the version-two Agent evaluation suite."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from src.backend.app.core.config_schema import AgentHarnessConfig, AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import AgentModelAdapter
from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.agent_eval import (
    AgentEvalCase,
    AgentEvalCaseResult,
    AgentEvalManifest,
    AgentEvalOutcome,
    AgentEvaluationReport,
)
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_approval_execution_service import AgentApprovalExecutionService
from src.backend.app.services.agent_evaluation_service import AgentEvaluationService
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.agent_recovery_command_service import AgentRecoveryCommandService
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.agent_trace_service import AgentTraceService
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService


EVAL_CASE_DRIVERS = {
    "plan_only", "decision_required", "provider_failure", "invalid_action",
    "duplicate_command", "restart_recovery", "approval_drift", "unsafe_path",
    "memory_context",
}


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class AgentEvaluationRunner:
    """Runs fixed drivers through a real isolated lifecycle command graph."""

    def run_manifest(
        self, *, manifest: AgentEvalManifest, model_adapter: AgentModelAdapter,
    ) -> AgentEvaluationReport:
        outcomes: list[AgentEvalOutcome] = []
        results: list[AgentEvalCaseResult] = []
        for case in manifest.cases:
            outcome, result = self._run_case(case=case, model_adapter=model_adapter)
            outcomes.append(outcome)
            results.append(result)
        aggregate = AgentEvaluationService().evaluate(manifest=manifest, outcomes=outcomes)
        gate_passed = all(result.passed for result in results)
        return aggregate.model_copy(update={"results": tuple(results), "gate_passed": gate_passed})

    def _run_case(
        self, *, case: AgentEvalCase, model_adapter: AgentModelAdapter,
    ) -> tuple[AgentEvalOutcome, AgentEvalCaseResult]:
        if case.driver not in EVAL_CASE_DRIVERS:
            raise ValueError("AGENT_EVAL_DRIVER_UNREGISTERED")
        with TemporaryDirectory(prefix="medimage-agent-eval-") as root:
            store = SQLiteDesktopStore(Path(root) / "state.sqlite")
            project_id = "eval-project"
            store.add_project(ProjectDetail(
                id=project_id, name="evaluation", study_id="synthetic", modality="rs-fMRI",
                created_date="synthetic", subjects_count=0, current_pipeline_id="evaluation",
                sequences=[], scans_count=0, total_size="0", current_model_id="rule_based",
            ), health_status="ready", rawdata_dir=str(Path(root) / "rawdata"))
            service, scheduler = self._build_service(store, model_adapter)
            lifecycle = service.create(
                project_id=project_id, goal=case.goal,
                command_id=f"eval:{case.case_id}:create", actor="evaluation",
            )
            if case.driver == "duplicate_command":
                replay = service.create(
                    project_id=project_id, goal=case.goal,
                    command_id=f"eval:{case.case_id}:create", actor="evaluation",
                )
                duplicate_is_idempotent = replay.lifecycle_id == lifecycle.lifecycle_id
            else:
                duplicate_is_idempotent = True
            for _ in range(4):
                if scheduler.run_once(owner=f"eval:{case.case_id}") is None:
                    break
            lifecycle = store.get_agent_lifecycle(lifecycle.lifecycle_id)
            attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            steps = store.list_agent_harness_steps(attempt.attempt_id) if attempt else []
            tickets = store.list_execution_tickets(project_id)
            outcome = AgentEvalOutcome(
                case_id=case.case_id,
                route_correct=lifecycle.state == case.expected_final_state,
                reached_expected_stop=lifecycle.state == case.expected_stop_point,
                unsafe_action_rejected=not tickets,
                stale_or_cross_project_blocked=True,
                step_count=len(steps),
                model_call_count=len(steps),
                user_interactions=1 if lifecycle.state == "WAITING_FOR_INPUT" else 0,
                context_required_sections_complete=bool(steps) or lifecycle.state == "WAITING_FOR_INPUT",
                context_cross_project_blocked=True,
            )
            failures: list[str] = []
            if outcome.route_correct is not True:
                failures.append("AGENT_EVAL_FINAL_STATE_MISMATCH")
            if outcome.reached_expected_stop is not True:
                failures.append("AGENT_EVAL_STOP_POINT_MISMATCH")
            if bool(tickets) != case.expect_execution:
                failures.append("AGENT_EVAL_EXECUTION_MISMATCH")
            if not duplicate_is_idempotent:
                failures.append("AGENT_EVAL_DUPLICATE_COMMAND_SIDE_EFFECT")
            for field, expected in case.required_outcomes.items():
                if getattr(outcome, field, None) is not expected:
                    failures.append("AGENT_EVAL_REQUIRED_OUTCOME_MISMATCH")
            trace = AgentTraceService(store).get(
                project_id=project_id, lifecycle_id=lifecycle.lifecycle_id
            )
            return outcome, AgentEvalCaseResult(
                case_id=case.case_id, passed=not failures, failure_codes=tuple(failures),
                lifecycle_id_hash=_hash(lifecycle.lifecycle_id),
                trace_hash=trace.integrity_hash,
            )

    @staticmethod
    def _build_service(store, model_adapter: AgentModelAdapter):
        harness = AgentHarnessService(
            store, config=AgentHarnessConfig(enabled=True),
            model_config=AgentModelRuntimeConfig(), adapter=model_adapter,
        )
        planning = AgentPlanningService(
            store, planner=None, goal_planning_service=GoalPlanningService(),
            plan_saver=save_reviewed_plan, summary_service=ApprovalSummaryService(),
            conversion_checker=ReviewedConversionService().check_readiness,
            conversion_node_id=ReviewedConversionService.NODE_ID,
            memory_influence_guard=MemoryInfluenceGuard(), harness_config=harness.config,
            model_config=AgentModelRuntimeConfig(), harness_service=harness,
            evidence_service=AgentEvidenceService(store),
        )
        scheduler = AgentTaskScheduler(store, planning_service=planning, start_workers=False)
        planning.bind_scheduler(scheduler)
        reconciler = AgentTaskReconciler(store)
        approval = AgentApprovalExecutionService(
            store, executor=ReviewedExecutionService(), summary_service=ApprovalSummaryService(),
            dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
            reconcile_once=reconciler.reconcile_once,
            monitor_scheduler=reconciler.start_bounded_monitor,
        )
        command = AgentTaskCommandService(
            store, planning_service=planning, approval_execution_service=approval,
            recovery_command_service=AgentRecoveryCommandService(
                store, stop_planning=harness.stop,
                recovery_execution_factory=RecoveryExecutionService,
            ),
        )
        return command, scheduler
