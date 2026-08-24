"""The approval-bound execution half of the Agent Task lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any, Callable

from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.approval_summary import ApprovalSummary
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_invariant_checker import AgentInvariantChecker
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService


logger = logging.getLogger(__name__)


class AgentApprovalExecutionService:
    """Verify an immutable approval before the sole execution service runs."""

    def __init__(
        self, store, *, executor: ReviewedExecutionService,
        summary_service: ApprovalSummaryService,
        dry_runner: Callable[..., dict[str, Any]],
        reconcile_once: Callable[..., object] | None = None,
        monitor_scheduler: Callable[..., bool] | None = None,
    ) -> None:
        self.store = store
        self.orchestrator = AgentOrchestrator(store)
        self.executor = executor
        self.summary_service = summary_service
        self.dry_runner = dry_runner
        reconciler = AgentTaskReconciler(store)
        self.reconcile_once = reconcile_once or reconciler.reconcile_once
        self.monitor_scheduler = monitor_scheduler or reconciler.start_bounded_monitor

    def approve(
        self, *, project_id: str, lifecycle_id: str, approval_summary_hash: str,
        command_id: str, actor: str,
    ):
        try:
            result = self._approve(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                approval_summary_hash=approval_summary_hash,
                command_id=command_id,
                actor=actor,
            )
        except Exception:
            logger.warning(
                "agent_approval_rejected",
                extra={"medimage": agent_log_context(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    event_code="AGENT_APPROVAL_REJECTED",
                )},
            )
            raise
        logger.info(
            "agent_approval_accepted",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                reviewed_plan_id=getattr(result, "reviewed_plan_id", None),
                execution_ticket_id=getattr(result, "execution_ticket_id", None),
                run_id=getattr(result, "run_id", None),
                event_code="AGENT_APPROVAL_ACCEPTED",
            )},
        )
        return result

    def _approve(
        self, *, project_id: str, lifecycle_id: str, approval_summary_hash: str,
        command_id: str, actor: str,
    ):
        current = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        AgentInvariantChecker(self.store).assert_clear(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
        )
        if current.state != "WAITING_FOR_APPROVAL" or not current.reviewed_plan_id:
            raise SafetyError("AGENT_APPROVAL_STATE_REQUIRED", code="AGENT_APPROVAL_STATE_REQUIRED")
        reviewed = self.store.get_reviewed_plan(current.reviewed_plan_id)
        project = self.store.get_project(project_id)
        if reviewed is None or project is None or reviewed.project_id != project_id:
            raise SafetyError("AGENT_APPROVAL_BINDING_MISSING", code="AGENT_APPROVAL_BINDING_MISSING")
        raw = reviewed.payload.get("approval_envelope")
        if not isinstance(raw, dict):
            raise SafetyError("APPROVAL_SUMMARY_MISSING", code="APPROVAL_SUMMARY_MISSING")
        summary = ApprovalSummary.model_validate(raw)
        self.summary_service.verify(summary)
        rebuilt = self.summary_service.build(
            project=project, reviewed_plan=reviewed, now=summary.issued_at,
            ttl_minutes=max(1, int((summary.expires_at - summary.issued_at).total_seconds() // 60)),
        )
        if (
            approval_summary_hash != summary.summary_hash
            or rebuilt.summary_hash != summary.summary_hash
            or reviewed.plan_hash != summary.plan_hash
        ):
            raise SafetyError("APPROVAL_SUMMARY_STALE", code="APPROVAL_SUMMARY_STALE")
        from src.backend.app.services.agent_execution_prerequisites import execution_prerequisite_issue

        prerequisite_issue = execution_prerequisite_issue(reviewed.payload["plan"])
        if prerequisite_issue is not None:
            raise SafetyError(
                f"AGENT_EXECUTION_PREREQUISITE_MISSING: {prerequisite_issue}",
                code="AGENT_EXECUTION_PREREQUISITE_MISSING",
            )
        from src.backend.app.api.execute_reviewed_routes import ExecuteReviewedRequest

        approval = {
            **summary.confirmations, "approved_by": actor,
            "approved_at": datetime.now(UTC).isoformat(),
            "approval_summary_hash": summary.summary_hash,
        }
        dry_run = self.dry_runner(
            plan=reviewed.payload["plan"], approval=approval, project_id=project_id,
            reviewed_plan_id=reviewed.reviewed_plan_id,
            project_config_path=reviewed.project_config_path, actor=actor,
            lifecycle_id=lifecycle_id,
        )
        if not dry_run.get("ok"):
            details: dict[str, Any] = {"blocked_status": str(dry_run.get("status") or "UNKNOWN")}
            issues = dry_run.get("errors") or dry_run.get("blocking_issues")
            if isinstance(issues, list):
                details["blocking_issues"] = [str(item) for item in issues]
            raise SafetyError(
                f"AGENT_DRY_RUN_BLOCKED: {details['blocked_status']}",
                code="AGENT_DRY_RUN_BLOCKED", details=details,
            )
        result = self.executor.execute(ExecuteReviewedRequest(
            plan=reviewed.payload["plan"], approval=approval, project_id=project_id,
            reviewed_plan_id=reviewed.reviewed_plan_id,
            project_config_path=reviewed.project_config_path, dry_run=False,
            persist_audit=True, write_pipeline_yaml=True, confirm_execution=True,
            actor=actor, lifecycle_id=lifecycle_id, command_id=command_id,
        ))
        if not result.get("ok"):
            current = self.orchestrator.get(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
            )
            if current.state == "RECOVERY_PROPOSED":
                return current
            details = {"blocked_status": str(result.get("status") or "UNKNOWN")}
            if result.get("recovery") is not None:
                details["recovery"] = result["recovery"]
            if details["blocked_status"] == "REVIEWED_EXECUTION_DISABLED":
                details.update(required_environment=["MEDIMAGE_ENABLE_REVIEWED_EXECUTION"], retryable_after_configuration=True)
            raise SafetyError(
                f"AGENT_EXECUTION_BLOCKED: {details['blocked_status']}",
                code="AGENT_EXECUTION_BLOCKED", details=details,
            )
        current = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if current.state == "RUNNING":
            current = self.reconcile_once(project_id=project_id, lifecycle_id=lifecycle_id)
            if current.state == "RUNNING":
                self.monitor_scheduler(project_id=project_id, lifecycle_id=lifecycle_id)
        return current
