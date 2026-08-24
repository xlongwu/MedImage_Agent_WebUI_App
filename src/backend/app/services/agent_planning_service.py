"""Persisted deterministic and controlled planning for Agent Tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import math
from typing import Any
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import REQUEST_BUILDER_VERSION, action_schema
from src.backend.app.schemas.agent_model import build_agent_model_profile
from src.backend.app.agent_skills.registry import AgentSkillRegistry, BUILTIN_SKILL_IDS
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.goal_contract_builder import build_goal_contract_semantics
from src.backend.app.planner.memory_influence_guard import (
    MemoryInfluenceError,
    MemoryInfluenceGuard,
)
from src.backend.app.planner.project_context import ProjectContextError, load_project_context
from src.backend.app.schemas.agent_lifecycle import DecisionItem, PendingDecisionBatch, PendingDecisionOption
from src.backend.app.schemas.goal_contract import GoalContractCandidate
from src.backend.app.schemas.memory import MemoryContext
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.schemas.planning import PlanningRequest
from src.backend.app.services.memory_repository import MemoryRepositoryError
from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService


logger = logging.getLogger(__name__)


class AgentPlanningService:
    """Own deterministic and controlled Agent planning transitions."""

    def __init__(
        self,
        store,
        *,
        planner: Callable[..., dict[str, Any]] | None,
        goal_planning_service: GoalPlanningService,
        plan_saver: Callable[..., Any],
        summary_service: ApprovalSummaryService,
        conversion_checker: Callable[..., dict[str, Any]],
        conversion_node_id: str,
        memory_context_service: MemoryRetrievalService | None = None,
        memory_initialization_error: str | None = None,
        memory_influence_guard: MemoryInfluenceGuard,
        harness_service: AgentHarnessService | None = None,
        harness_config,
        model_config: AgentModelRuntimeConfig | None = None,
        evidence_service: AgentEvidenceService,
        conversion_preparer: Callable[..., Any] | None = None,
        scheduler=None,
    ) -> None:
        self.store = store
        self.orchestrator = AgentOrchestrator(store)
        self.planner = planner
        self.goal_planning_service = goal_planning_service
        self.plan_saver = plan_saver
        self.summary_service = summary_service
        self.conversion_checker = conversion_checker
        self.conversion_node_id = conversion_node_id
        self.memory_influence_guard = memory_influence_guard
        self.memory_context_service = memory_context_service
        self.harness_service = harness_service
        self.evidence_service = evidence_service
        self.conversion_preparer = conversion_preparer or self._prepare_dicom_conversion
        self.harness_config = harness_config
        self.model_config = model_config or AgentModelRuntimeConfig()
        self.memory_initialization_error = memory_initialization_error
        self.scheduler = scheduler

    def bind_scheduler(self, scheduler) -> None:
        """Finish the intentional scheduler/planner cycle in application assembly."""
        self.scheduler = scheduler

    def bind_harness(self, harness_service: AgentHarnessService) -> None:
        """Bind the preassembled advice-only coordinator."""
        self.harness_service = harness_service

    def create(self, *, project_id: str, goal: str, command_id: str, actor: str):
        replay = self._command_replay(project_id, command_id)
        if replay is not None:
            return replay
        goal = goal.strip()
        lifecycle = self.orchestrator.create(
            project_id=project_id,
            command_id=command_id,
            actor=actor,
            goal_text=goal,
            goal_hash=stable_hash({"goal": goal}),
            planning_wake_reason="create",
        )
        self._notify_scheduler()
        return lifecycle

    def answer(
        self,
        *, project_id: str,
        lifecycle_id: str,
        batch_id: str,
        answers: tuple | list,
        command_id: str,
        actor: str,
    ):
        replay = self._command_replay(project_id, command_id)
        if replay is not None:
            return replay
        current = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        pending = current.pending_decision_batch
        if current.state not in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"} or pending is None:
            raise SafetyError("AGENT_DECISION_NOT_PENDING", code="AGENT_DECISION_NOT_PENDING")
        if pending.batch_id != batch_id:
            raise SafetyError("AGENT_DECISION_STALE", code="AGENT_DECISION_STALE")
        if pending.expires_at <= datetime.now(UTC):
            raise SafetyError("AGENT_DECISION_BATCH_EXPIRED", code="AGENT_DECISION_BATCH_EXPIRED")
        if pending.plan_hash_before and current.command_context.get("pending_plan_hash") != pending.plan_hash_before:
            raise SafetyError("AGENT_DECISION_PLAN_STALE", code="AGENT_DECISION_PLAN_STALE")
        fresh = self.evidence_service.build_snapshot(
            project_id=project_id, lifecycle_id=lifecycle_id,
            memory_context=self._memory_context(current.command_context),
        )
        if fresh.snapshot_hash != pending.evidence_snapshot_hash:
            raise SafetyError("AGENT_DECISION_EVIDENCE_STALE", code="AGENT_DECISION_EVIDENCE_STALE")
        supplied: dict[str, str] = {}
        errors: dict[str, str] = {}
        for raw in answers:
            item_id = str(getattr(raw, "item_id", raw.get("item_id") if isinstance(raw, dict) else ""))
            value = str(getattr(raw, "value", raw.get("value") if isinstance(raw, dict) else "")).strip()
            if not item_id or item_id in supplied:
                errors[item_id or "answers"] = "duplicate_or_empty_item"
            else:
                supplied[item_id] = value
        items = {item.item_id: item for item in pending.items}
        for item_id, item in items.items():
            value = supplied.get(item_id, "")
            if item.required and not value:
                errors[item_id] = "required"
            elif item.answer_type == "option" and value not in {option.id for option in item.options}:
                errors[item_id] = "invalid_option"
            elif item.answer_type == "boolean" and value not in {"true", "false"}:
                errors[item_id] = "invalid_boolean"
            elif item.answer_type == "number":
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    errors[item_id] = "invalid_number"
                else:
                    if not math.isfinite(number):
                        errors[item_id] = "invalid_number"
                    elif item.min_value is not None and number < item.min_value:
                        errors[item_id] = "below_minimum"
                    elif item.max_value is not None and number > item.max_value:
                        errors[item_id] = "above_maximum"
        for item_id in supplied:
            if item_id not in items:
                errors[item_id] = "unknown_item"
        if errors:
            raise SafetyError("AGENT_DECISION_BATCH_INVALID", code="AGENT_DECISION_BATCH_INVALID", details={"fields": errors})
        context = dict(current.command_context)
        updates: dict[str, Any] = {"pending_decision_batch": None, "command_context": context}
        context.pop("pending_plan_hash", None)
        goal_items = [item for item in pending.items if item.kind == "goal_revision"]
        if goal_items:
            revised_goal = supplied[goal_items[0].item_id]
            if not revised_goal:
                raise SafetyError("AGENT_GOAL_REVISION_REQUIRED", code="AGENT_GOAL_REVISION_REQUIRED")
            context.pop("science_answers", None)
            context["revision_reason"] = "goal_revised"
            updates.update(
                goal_text=revised_goal,
                goal_hash=stable_hash({"goal": revised_goal}),
                command_context=context,
            )
        else:
            science_answers = dict(context.get("science_answers") or {})
            for item in pending.items:
                value = supplied.get(item.item_id, "")
                if item.kind == "dicom_conversion":
                    if value != "approve_conversion":
                        raise SafetyError(
                            "DICOM conversion approval is required before this goal can continue.",
                            code="AGENT_DICOM_CONVERSION_APPROVAL_REQUIRED",
                        )
                    prepared = self.conversion_preparer(
                        store=self.store,
                        project_id=project_id,
                        actor=actor,
                    )
                    if not bool(getattr(prepared, "execution_ready", False)):
                        details = {
                            "status": getattr(prepared, "status", "blocked"),
                            "blocking_issues": list(
                                getattr(prepared, "blocking_issues", ()) or ()
                            ),
                            "errors": list(getattr(prepared, "errors", ()) or ()),
                        }
                        raise SafetyError(
                            "DICOM conversion preparation did not satisfy the controlled execution gates.",
                            code="AGENT_DICOM_CONVERSION_PREPARE_BLOCKED",
                            details=details,
                        )
                    context["dicom_conversion_preparation"] = {
                        "conversion_run_id": str(
                            getattr(prepared, "conversion_run_id", "") or ""
                        ),
                        "approval_id": str(getattr(prepared, "approval_id", "") or ""),
                        "release_approval_id": str(
                            getattr(prepared, "release_approval_id", "") or ""
                        ),
                        "status": str(getattr(prepared, "status", "ready")),
                    }
                    continue
                if item.source == "memory_suggestion" and value == "__ignore_memory__":
                    ignored = set(context.get("ignored_memory_ids") or [])
                    if item.memory_id:
                        ignored.add(item.memory_id)
                    context["ignored_memory_ids"] = sorted(ignored)
                else:
                    science_answers[item.kind] = value
            context["science_answers"] = science_answers
            context["revision_reason"] = "decision_answered"
        target = "CONTEXT_READY" if current.state == "WAITING_FOR_INPUT" else "PLAN_DRAFTED"
        resumed = self.orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state=target,
            command_id=command_id,
            actor=actor,
            source_command="answer",
            updates=updates,
            details={"batch_id": batch_id, "item_ids": sorted(supplied)},
            planning_wake_reason="answer",
        )
        self._notify_scheduler()
        return resumed

    @staticmethod
    def _prepare_dicom_conversion(*, store, project_id: str, actor: str):
        """Persist the canonical conversion review package; never dispatch execution."""

        from src.backend.app.schemas.dicom_conversion_prepare import (
            DicomConversionPrepareConfirmations,
            DicomConversionPrepareRequest,
        )
        from src.backend.app.services.dicom_conversion_prepare import (
            run_dicom_conversion_prepare,
        )

        return run_dicom_conversion_prepare(
            store,
            project_id,
            DicomConversionPrepareRequest(
                approved_by=actor,
                overwrite_policy="fail_if_exists",
                confirmations=DicomConversionPrepareConfirmations(
                    mappings_reviewed=True,
                    rawdata_readonly=True,
                    research_use_only=True,
                    no_clinical_use=True,
                    native_converter=True,
                    rollback_policy=True,
                    risk_acknowledgement=True,
                    approval_audit=True,
                    public_endpoint=True,
                    frontend_execute=True,
                    spm_dpabi_matlab_disabled=True,
                    confirm_execution=True,
                ),
            ),
        )

    def advance_planning(
        self, *, project_id: str, lifecycle_id: str, wake_reason: str
    ):
        """Advance exactly one durable planning wake without execution authority."""
        lifecycle = self.orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if lifecycle.state not in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "PLAN_VALIDATED", "WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"}:
            return lifecycle
        logger.info(
            "agent_planning_started",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                event_code="AGENT_PLANNING_STARTED",
            )},
        )
        try:
            recovered = self._resume_persisted_plan(
                lifecycle=lifecycle, actor="system-agent-task-scheduler"
            )
            result = recovered or self._harness_or_plan(
                lifecycle=lifecycle,
                command_id=f"planning-wake:{lifecycle.lifecycle_id}:{lifecycle.updated_at.isoformat()}",
                actor="system-agent-task-scheduler",
                resume=wake_reason != "create",
                wake_reason=wake_reason,
            )
        except Exception:
            logger.warning(
                "agent_planning_blocked",
                extra={"medimage": agent_log_context(
                    project_id=project_id,
                    lifecycle_id=lifecycle_id,
                    reviewed_plan_id=lifecycle.reviewed_plan_id,
                    event_code="AGENT_PLANNING_BLOCKED",
                )},
            )
            raise
        logger.info(
            "agent_planning_completed",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                reviewed_plan_id=getattr(result, "reviewed_plan_id", None),
                event_code="AGENT_PLANNING_COMPLETED",
            )},
        )
        return result

    def draft_plan(self, *, lifecycle, command_id: str, actor: str):
        """Typed deterministic callback used by the planning-action service."""
        return self._plan(lifecycle=lifecycle, command_id=command_id, actor=actor, resume=True)

    def _enqueue_planning(self, *, lifecycle, reason: str) -> None:
        if self.scheduler is None:
            raise RuntimeError("AGENT_TASK_SCHEDULER_NOT_BOUND")
        self.scheduler.enqueue(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            step_key=f"{lifecycle.state}:{lifecycle.updated_at.isoformat()}",
            reason=reason,
        )

    def _notify_scheduler(self) -> None:
        if self.scheduler is None:
            raise RuntimeError("AGENT_TASK_SCHEDULER_NOT_BOUND")
        self.scheduler.notify()

    def enqueue_resume(self, *, lifecycle, reason: str, details=None) -> bool:
        """Persist a post-transition planning continuation from an owning service."""
        self._enqueue_planning(lifecycle=lifecycle, reason=reason)
        return True

    def _harness_or_plan(
        self, *, lifecycle, command_id: str, actor: str, resume: bool = False,
        wake_reason: str = "planning",
    ):
        """Select the controlled or deterministic planning path for one wake."""
        project = self.store.get_project(lifecycle.project_id)
        metadata = project.metadata if project is not None and isinstance(project.metadata, dict) else {}
        provider = self.model_config.provider
        if not self.harness_config.enabled and self.harness_service is None:
            return self._plan(lifecycle=lifecycle, command_id=command_id, actor=actor, resume=resume)
        existing_attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
        if existing_attempt is not None and existing_attempt.fallback_to is not None:
            return self._plan(lifecycle=lifecycle, command_id=command_id, actor=actor, resume=resume)
        harness = self.harness_service
        if harness is None:
            raise RuntimeError("AGENT_HARNESS_NOT_BOUND")
        if harness.draft_plan is None:
            harness.draft_plan = lambda **kwargs: self._plan(resume=resume, **kwargs)
        if resume:
            harness.prepare_resume(lifecycle=lifecycle, provider_ref=provider)
        else:
            harness.ensure_attempt(lifecycle=lifecycle, provider_ref=provider)
        result = harness.run_until_blocked(
            lifecycle=lifecycle,
            actor=actor,
            wake_reason=wake_reason,
            lease_owner=f"agent-planning:{lifecycle.lifecycle_id}",
        )
        return result.lifecycle

    def _resume_persisted_plan(self, *, lifecycle, actor: str):
        """Finish a persisted plan checkpoint without invoking the planner again.

        A process can stop after ``save_reviewed_plan`` but before binding the
        plan to the lifecycle or rebuilding its approval projection.  The
        persisted PlanningRequest is the recovery fence: it identifies the
        exact lifecycle that owns the plan and prevents a second model or
        deterministic planning invocation.
        """
        if lifecycle.state not in {"PLAN_DRAFTED", "PLAN_VALIDATED"}:
            return None
        reviewed = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id) if lifecycle.reviewed_plan_id else None
        if reviewed is None:
            getter = getattr(self.store, "list_reviewed_plans", None)
            if not callable(getter):
                return None
            reviewed = next(
                (
                    record
                    for record in getter(lifecycle.project_id)
                    if isinstance(record.payload.get("planning_request"), dict)
                    and record.payload["planning_request"].get("lifecycle_id") == lifecycle.lifecycle_id
                ),
                None,
            )
        if reviewed is None or reviewed.project_id != lifecycle.project_id:
            return None
        plan = reviewed.payload.get("plan")
        if not isinstance(plan, dict):
            return None
        plan_only = self._is_plan_only(plan)
        payload = dict(reviewed.payload)
        if plan_only:
            payload.update(
                dry_run={"ok": True, "status": "NOT_RUN_PLAN_ONLY", "execution_performed": False},
                execution_status="NOT_EXECUTED_PLAN_ONLY",
                execution_performed=False,
                rawdata_modified=False,
            )
        elif not isinstance(payload.get("approval_envelope"), dict):
            project = self.store.get_project(lifecycle.project_id)
            if project is None:
                raise SafetyError("PROJECT_NOT_FOUND", code="PROJECT_NOT_FOUND")
            summary = self.summary_service.build(project=project, reviewed_plan=reviewed)
            payload.update(
                approval_summary=self._public_summary(summary),
                approval_envelope=summary.model_dump(mode="json"),
                dry_run={"ok": None, "status": "PENDING_USER_APPROVAL", "execution_performed": False},
            )
        reviewed = self.store.update_reviewed_plan(reviewed.reviewed_plan_id, payload=payload) or reviewed
        if lifecycle.state == "PLAN_DRAFTED":
            lifecycle = self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="PLAN_VALIDATED",
                command_id=f"planning-recovery:{lifecycle.lifecycle_id}:validated",
                actor=actor,
                source_command="persisted_plan_recovered",
                updates={
                    "reviewed_plan_id": reviewed.reviewed_plan_id,
                    "goal_contract_id": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_id") or "") or None,
                    "goal_contract_hash": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_hash") or "") or None,
                },
                details={"recovered_without_replanning": True},
            )
        if plan_only:
            if lifecycle.state != "PLAN_VALIDATED":
                return lifecycle
            return self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="SUCCEEDED",
                command_id=f"planning-recovery:{lifecycle.lifecycle_id}:plan-only-complete",
                actor=actor,
                source_command="persisted_plan_recovered",
                details={"execution_performed": False, "recovered_without_replanning": True},
            )
        if lifecycle.state != "PLAN_VALIDATED":
            return lifecycle
        return self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="WAITING_FOR_APPROVAL",
            command_id=f"planning-recovery:{lifecycle.lifecycle_id}:approval",
            actor=actor,
            source_command="persisted_plan_recovered",
            details={"recovered_without_replanning": True},
        )

    @staticmethod
    def _public_summary(summary) -> dict[str, Any]:
        return {
            key: value
            for key, value in summary.model_dump(mode="json").items()
            if key in {
                "summary_hash", "execution_environment_snapshot_id", "execution_environment_hash", "goal", "dataset_summary", "execution_summary", "write_roots",
                "rawdata_read_only", "external_tools", "limitations", "science_changes", "sections", "expires_at",
                "memory_context_hash", "memory_refs", "memory_influence_summary",
                "planning_inputs_hash", "revision_no", "parent_reviewed_plan_id", "parent_plan_hash", "revision_reason", "resource_policy",
            }
        }

    def _plan(self, *, lifecycle, command_id: str, actor: str, resume: bool = False):
        prepared = self._build_planning_request(
            lifecycle=lifecycle, command_id=command_id, actor=actor, resume=resume
        )
        if not isinstance(prepared, tuple):
            return prepared
        lifecycle, request, memory_context, metadata, project = prepared
        conversion_decision = self._require_dicom_preparation_decision(
            lifecycle=lifecycle,
            request=request,
            command_id=command_id,
            actor=actor,
        )
        if conversion_decision is not None:
            return conversion_decision
        result = self._generate_candidate_plan(request=request)
        if not result.get("ok") or not isinstance(result.get("plan"), dict):
            reason = "; ".join(str(item) for item in result.get("errors", [])) or "Planner unavailable"
            waiter = self._wait_for_goal_revision if self._requires_goal_revision(reason) else self._wait_for_input
            return waiter(
                lifecycle=lifecycle,
                command_id=f"{command_id}:planner",
                actor=actor,
                reason=reason,
            )
        return self._validate_persist_and_advance(
            lifecycle=lifecycle,
            command_id=command_id,
            actor=actor,
            project=project,
            metadata=metadata,
            memory_context=memory_context,
            request=request,
            result=result,
        )

    def _require_dicom_preparation_decision(
        self, *, lifecycle, request: PlanningRequest, command_id: str, actor: str
    ):
        """Stop raw-DICOM workflows at an explicit review before plan generation."""

        goal = str(request.goal or "").casefold()
        if not any(
            term in goal
            for term in (
                "functional connectivity",
                "功能连接",
                "生成 fc",
                "计算 fc",
                "run fc",
                "generate fc",
                "preprocess",
                "预处理",
            )
        ):
            return None
        try:
            project_context = load_project_context(
                project_id=request.project_id,
                project_config_path=request.project_config_path,
                store=self.store,
            )
        except ProjectContextError:
            return None
        diagnostics = dict(project_context.diagnostics)
        try:
            dicom_count = int(diagnostics.get("dicom_file_count") or 0)
        except (TypeError, ValueError):
            dicom_count = 0
        try:
            nifti_count = int(
                diagnostics.get("nifti_file_count")
                or diagnostics.get("nifti_files")
                or 0
            )
        except (TypeError, ValueError):
            nifti_count = 0
        if (
            dicom_count <= 0
            or nifti_count > 0
            or (
                diagnostics.get("agent_conversion_execution_ready") is True
                and diagnostics.get("agent_conversion_run_id")
            )
        ):
            return None
        decision = PendingDecisionBatch(
            batch_id=f"decision_batch_{uuid4().hex}",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            evidence_snapshot_hash=request.evidence_snapshot_hash,
            items=(self._dicom_conversion_decision_item(dicom_count),),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            source="planner",
        )
        return self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="WAITING_FOR_SCIENCE_DECISION",
            command_id=f"{command_id}:dicom-preparation:{decision.batch_id}",
            actor=actor,
            source_command="science_decision_required",
            updates={
                "pending_decision_batch": decision,
                "evidence_snapshot_hash": request.evidence_snapshot_hash,
                "command_context": dict(lifecycle.command_context),
            },
            details={"decision_kind": "dicom_conversion"},
        )

    def _build_planning_request(self, *, lifecycle, command_id: str, actor: str, resume: bool):
        project = self.store.get_project(lifecycle.project_id)
        metadata = project.metadata if project is not None and isinstance(project.metadata, dict) else {}
        project_config_path = metadata.get("project_config_path")
        if not isinstance(project_config_path, str) or not project_config_path:
            return self._wait_for_input(
                lifecycle=lifecycle,
                command_id=command_id,
                actor=actor,
                reason="A registered project configuration is required before planning.",
            )
        command_context = dict(lifecycle.command_context)
        if self.memory_initialization_error is not None:
            raise SafetyError(
                self.memory_initialization_error,
                code=self.memory_initialization_error,
            )
        memory_context: MemoryContext | None = None
        raw_memory_context = command_context.get("memory_context")
        if isinstance(raw_memory_context, dict):
            memory_context = MemoryContext.model_validate(raw_memory_context)
        elif self.memory_context_service is not None:
            try:
                memory_context, retrieval_warnings = (
                    self.memory_context_service.build_context_with_warnings(
                        project_id=lifecycle.project_id,
                        goal=str(lifecycle.goal_text or ""),
                    )
                )
            except MemoryRepositoryError as exc:
                raise SafetyError(str(exc), code=exc.code) from exc
            command_context["memory_context"] = memory_context.model_dump(mode="json")
            if hasattr(self.store, "get_memory_consent"):
                consent = self.store.get_memory_consent(lifecycle.project_id)
                memory_config = getattr(self.memory_context_service, "config", None)
                command_context["memory_consent"] = {
                    "available": bool(
                        memory_config is not None and memory_config.enabled
                    ),
                    "generate_enabled": bool(consent.get("generate_enabled")),
                    "use_enabled": bool(consent.get("use_enabled")),
                    "consent_epoch": int(consent.get("consent_epoch") or 0),
                    "status": memory_context.status,
                }
            command_context["memory_warnings"] = list(dict.fromkeys(retrieval_warnings))
        if lifecycle.state in {"CREATED", "WAITING_FOR_INPUT"}:
            lifecycle = self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="CONTEXT_READY",
                command_id=f"{command_id}:context",
                actor=actor,
                source_command="context_ready",
                updates={"command_context": command_context},
            )
        evidence = self.evidence_service.build_snapshot(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            memory_context=memory_context,
        )
        command_context["evidence_snapshot_hash"] = evidence.snapshot_hash
        lifecycle = lifecycle.model_copy(
            update={"command_context": command_context, "evidence_snapshot_hash": evidence.snapshot_hash}
        )
        parent = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id) if lifecycle.reviewed_plan_id else None
        request = PlanningRequest(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            goal=str(lifecycle.goal_text or ""),
            project_config_path=project_config_path,
            evidence_snapshot_hash=evidence.snapshot_hash,
            science_answers={
                str(key): str(value)
                for key, value in dict(lifecycle.command_context.get("science_answers") or {}).items()
            },
            memory_context_hash=(memory_context.context_hash if memory_context else None),
            memory_context_refs=(
                tuple(item.model_dump(mode="json") for item in memory_context.evidence_refs)
                if memory_context else ()
            ),
            parent_reviewed_plan_id=(parent.reviewed_plan_id if parent else None),
            parent_plan_hash=(parent.plan_hash if parent else None),
            revision_reason=str(
                lifecycle.command_context.get(
                    "revision_reason", "decision_answered" if resume else "initial"
                )
            ),
            provider_ref=self.model_config.provider,
            prompt_version="agent-harness-prompt-v3",
            model_profile_hash=self._model_profile_hash(),
        )
        return lifecycle, request, memory_context, metadata, project

    def _model_profile_hash(self) -> str:
        registry = AgentSkillRegistry()
        refs = tuple(registry.load(skill_id).reference for skill_id in BUILTIN_SKILL_IDS)
        return build_agent_model_profile(
            self.model_config,
            prompt_template_version="agent-harness-prompt-v3",
            skill_refs=refs,
            action_schema=action_schema(),
            context_policy_version="agent-context-v3",
            request_builder_version=REQUEST_BUILDER_VERSION,
        ).profile_hash

    def _generate_candidate_plan(self, *, request: PlanningRequest) -> dict[str, Any]:
        if self.planner is not None:
            return self.planner(request=request)
        return self.goal_planning_service.plan(request=request, store=self.store)

    def _validate_persist_and_advance(
        self, *, lifecycle, command_id: str, actor: str, project, metadata: dict[str, Any],
        memory_context: MemoryContext | None, request: PlanningRequest, result: dict[str, Any],
    ):
        plan = self._apply_science_answers(result["plan"], lifecycle.command_context, metadata)
        command_context = dict(lifecycle.command_context)
        if lifecycle.state == "CONTEXT_READY":
            lifecycle = self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="PLAN_DRAFTED",
                command_id=f"{command_id}:draft",
                actor=actor,
                source_command="plan_drafted",
                updates={"command_context": command_context, "evidence_snapshot_hash": request.evidence_snapshot_hash},
                details={"plan_hash": stable_hash(plan), "provider": request.provider_ref},
            )
        decision = self._decision_batch(
            plan, command_context, metadata, request.evidence_snapshot_hash, memory_context, lifecycle
        )
        if decision is not None:
            command_context["pending_plan_hash"] = decision.plan_hash_before
            return self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="WAITING_FOR_SCIENCE_DECISION",
                command_id=f"{command_id}:decision:{decision.batch_id}",
                actor=actor,
                source_command="science_decision_required",
                updates={"pending_decision_batch": decision, "evidence_snapshot_hash": request.evidence_snapshot_hash, "command_context": command_context},
            )
        try:
            self.memory_influence_guard.validate(
                plan=plan,
                memory_context=memory_context,
                science_answers=dict(command_context.get("science_answers") or {}),
                project_context_values=dict(metadata.get("agent_science_decisions") or {}),
            )
        except MemoryInfluenceError as exc:
            raise SafetyError(str(exc), code=exc.code) from exc
        validation = result.get("validation") or {}
        if not validation.get("ok"):
            return self._wait_for_input(
                lifecycle=lifecycle,
                command_id=f"{command_id}:validation",
                actor=actor,
                reason="Plan validation failed; revise the goal or project inputs.",
            )
        plan_only = self._is_plan_only(plan)
        conversion_readiness = {"ok": True, "status": "NOT_REQUIRED_PLAN_ONLY"}
        if not plan_only:
            conversion_readiness = self._conversion_readiness(plan)
        if not conversion_readiness.get("ok"):
            blockers = conversion_readiness.get("blocking_issues") or [
                conversion_readiness.get("status", "conversion package is not ready")
            ]
            return self._wait_for_input(
                lifecycle=lifecycle,
                command_id=f"{command_id}:conversion-readiness",
                actor=actor,
                reason="DICOM conversion preparation is required: "
                + "; ".join(str(item) for item in blockers),
            )
        dry_run = (
            {"ok": True, "status": "NOT_RUN_PLAN_ONLY", "execution_performed": False}
            if plan_only
            else {
                "ok": None,
                "status": "PENDING_USER_APPROVAL",
                "execution_performed": False,
            }
        )
        contract_build = build_goal_contract_semantics(plan, lifecycle.goal_text)
        if not contract_build.ok or not contract_build.semantics:
            return self._wait_for_goal_revision(
                lifecycle=lifecycle,
                command_id=f"{command_id}:goal-contract",
                actor=actor,
                reason=contract_build.reason or "Goal Contract clarification required",
            )
        candidate = GoalContractCandidate.model_validate(contract_build.semantics)
        reviewed = self.plan_saver(
            project_id=lifecycle.project_id,
            project_config_path=request.project_config_path,
            plan=plan,
            validation=validation,
            goal=lifecycle.goal_text,
            provider=request.provider_ref,
            status="REVIEWED",
            warnings=list(result.get("warnings", [])),
            goal_contract_candidate=candidate,
            reviewed_actor=actor,
            memory_context=memory_context,
            planner_invocation=result.get("planner_invocation"),
            planner_evidence=result.get("planner_evidence"),
            planning_request=request,
            store=self.store,
        )
        if plan_only:
            payload = dict(reviewed.payload)
            payload.update(
                dry_run=dry_run,
                execution_status="NOT_EXECUTED_PLAN_ONLY",
                execution_performed=False,
                rawdata_modified=False,
            )
            reviewed = self.store.update_reviewed_plan(reviewed.reviewed_plan_id, payload=payload) or reviewed
            lifecycle = self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="PLAN_VALIDATED",
                command_id=f"{command_id}:validated",
                actor=actor,
                source_command="plan_validated",
                updates={
                    "reviewed_plan_id": reviewed.reviewed_plan_id,
                    "goal_contract_id": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_id") or "") or None,
                    "goal_contract_hash": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_hash") or "") or None,
                },
                details={"plan_only": True, "execution_performed": False},
            )
            return self.orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="SUCCEEDED",
                command_id=f"{command_id}:plan-only-complete",
                actor=actor,
                source_command="plan_only_completed",
                details={
                    "reviewed_plan_id": reviewed.reviewed_plan_id,
                    "capability_level": "metadata_only",
                    "execution_performed": False,
                    "rawdata_modified": False,
                },
            )
        summary = self.summary_service.build(project=project, reviewed_plan=reviewed)
        public_summary = {
            key: value
            for key, value in summary.model_dump(mode="json").items()
            if key in {
                "summary_hash", "execution_environment_snapshot_id", "execution_environment_hash", "goal", "dataset_summary", "execution_summary", "write_roots",
                "rawdata_read_only", "external_tools", "limitations", "science_changes", "sections", "expires_at",
                "memory_context_hash", "memory_refs", "memory_influence_summary",
                "planning_inputs_hash", "revision_no", "parent_reviewed_plan_id", "parent_plan_hash", "revision_reason", "resource_policy",
            }
        }
        payload = dict(reviewed.payload)
        payload.update(
            approval_summary=public_summary,
            approval_envelope=summary.model_dump(mode="json"),
            dry_run=dry_run,
        )
        reviewed = self.store.update_reviewed_plan(reviewed.reviewed_plan_id, payload=payload) or reviewed
        lifecycle = self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="PLAN_VALIDATED",
            command_id=f"{command_id}:validated",
            actor=actor,
            source_command="plan_validated",
            updates={
                "reviewed_plan_id": reviewed.reviewed_plan_id,
                "goal_contract_id": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_id") or "") or None,
                "goal_contract_hash": str((reviewed.payload.get("goal_contract") or {}).get("goal_contract_hash") or "") or None,
            },
        )
        return self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="WAITING_FOR_APPROVAL",
            command_id=f"{command_id}:approval",
            actor=actor,
            source_command="approval_summary_ready",
            details={"approval_summary_hash": summary.summary_hash},
        )

    def _wait_for_input(self, *, lifecycle, command_id: str, actor: str, reason: str):
        if lifecycle.state not in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"}:
            raise SafetyError(reason, code="AGENT_PLANNING_BLOCKED")
        evidence = self.evidence_service.build_snapshot(project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)
        decision = PendingDecisionBatch(
            batch_id=f"decision_batch_{uuid4().hex}", lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id, evidence_snapshot_hash=evidence.snapshot_hash,
            expires_at=datetime.now(UTC) + timedelta(hours=24), items=(DecisionItem(
            item_id="missing_input",
            kind="missing_input",
            question="Resolve the project input required to continue.",
            impact=reason,
            answer_type="text",
        ),))
        return self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="WAITING_FOR_INPUT",
            command_id=command_id,
            actor=actor,
            source_command="input_required",
            reason=reason,
            updates={"pending_decision_batch": decision, "evidence_snapshot_hash": evidence.snapshot_hash},
        )

    def _wait_for_goal_revision(self, *, lifecycle, command_id: str, actor: str, reason: str):
        if lifecycle.state not in {"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"}:
            raise SafetyError(reason, code="AGENT_PLANNING_BLOCKED")
        evidence = self.evidence_service.build_snapshot(project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)
        decision = self._goal_revision_batch(lifecycle, evidence.snapshot_hash, reason)
        return self.orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="WAITING_FOR_INPUT",
            command_id=command_id,
            actor=actor,
            source_command="goal_revision_required",
            reason=reason,
            updates={"pending_decision_batch": decision, "evidence_snapshot_hash": evidence.snapshot_hash},
        )

    @staticmethod
    def _requires_goal_revision(reason: str) -> bool:
        normalized = reason.upper()
        return any(
            code in normalized
            for code in (
                "UNSUPPORTED_GOAL",
                "GOAL_KIND_UNSUPPORTED_OR_AMBIGUOUS",
                "GOAL_TEXT_REQUIRED",
                "EMPTY_GOAL",
            )
        )

    @staticmethod
    def _is_plan_only(plan: dict[str, Any]) -> bool:
        metadata = plan.get("metadata")
        return bool(
            isinstance(metadata, dict)
            and metadata.get("plan_only") is True
            and metadata.get("execution_enabled") is False
            and metadata.get("capability_level") == "metadata_only"
        )

    @staticmethod
    def _apply_science_answers(
        plan: dict[str, Any],
        context: dict[str, Any],
        project_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        copied = {**plan, "nodes": [dict(node) for node in plan.get("nodes", [])]}
        answers = dict(context.get("science_answers") or {})
        signals = AgentPlanningService._science_signals(copied, project_metadata)
        tr_values = signals.get("tr_conflict")
        if not isinstance(tr_values, dict):
            tr_values = {}
        for node in copied["nodes"]:
            params = dict(node.get("params") or {})
            if (
                node.get("id") == "native_preproc_full_execute"
                and "subject_id" in answers
            ):
                params["subject_id"] = str(answers["subject_id"])
            if node.get("id") == "functional_connectivity_subject" and "atlas" in answers:
                params["atlas_path"] = answers["atlas"]
            if node.get("id") == "native_preproc_full_execute" and "atlas" in answers:
                params["atlas"] = answers["atlas"]
            if (
                node.get("id") in {"native_preproc_full_execute", "nuisance_regression_subject"}
                and "global_signal_regression" in answers
            ):
                selected = answers["global_signal_regression"] == "include"
                params["include_global_signal"] = selected
            if (
                node.get("id")
                in {
                    "native_preproc_full_execute",
                    "temporal_filtering_subject",
                    "alff_falff_subject",
                }
                and "repetition_time" in answers
            ):
                selected_tr = tr_values.get(answers["repetition_time"])
                if selected_tr is not None:
                    params["tr"] = selected_tr
            if node.get("id") == "native_preproc_full_execute" and "template" in answers:
                params["template"] = answers["template"]
            if "overwrite" in answers:
                params["overwrite_policy"] = answers["overwrite"]
            if "experimental_backend" in answers and str(node.get("backend") or "").lower().startswith("gpu"):
                if answers["experimental_backend"] == "use_cpu":
                    node["backend"] = str(signals.get("cpu_backend") or "python-cpu")
                else:
                    params["experimental_backend_approved"] = True
            node["params"] = params
        return copied

    @staticmethod
    def _science_signals(
        plan: dict[str, Any], project_metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Merge explicit project and plan decision signals without guessing science choices."""
        signals: dict[str, Any] = {}
        if isinstance(project_metadata, dict):
            configured = project_metadata.get("agent_science_decisions")
            if isinstance(configured, dict):
                signals.update(configured)
        plan_metadata = plan.get("metadata")
        if isinstance(plan_metadata, dict):
            configured = plan_metadata.get("science_decisions")
            if isinstance(configured, dict):
                signals.update(configured)
        return signals

    @staticmethod
    def _science_decision_items(
        plan: dict[str, Any],
        context: dict[str, Any],
        project_metadata: dict[str, Any] | None = None,
    ) -> list[DecisionItem]:
        answers = dict(context.get("science_answers") or {})
        signals = AgentPlanningService._science_signals(plan, project_metadata)
        items: list[DecisionItem] = []
        plan_context = plan.get("project_context")
        diagnostics = (
            dict(plan_context.get("diagnostics") or {})
            if isinstance(plan_context, dict)
            else {}
        )
        try:
            dicom_count = int(diagnostics.get("dicom_file_count") or 0)
        except (TypeError, ValueError):
            dicom_count = 0
        try:
            nifti_count = int(
                diagnostics.get("nifti_file_count")
                or diagnostics.get("nifti_files")
                or 0
            )
        except (TypeError, ValueError):
            nifti_count = 0
        conversion_ready = bool(
            diagnostics.get("agent_conversion_execution_ready") is True
            and diagnostics.get("agent_conversion_run_id")
        )
        if dicom_count > 0 and nifti_count == 0 and not conversion_ready:
            items.append(AgentPlanningService._dicom_conversion_decision_item(dicom_count))
        if signals.get("subject_selection_required") and "subject_id" not in answers:
            candidates = signals.get("subject_candidates")
            options = tuple(
                PendingDecisionOption(
                    id=str(subject_id),
                    label=str(subject_id),
                    description=f"Run the reviewed native preprocessing scope only for {subject_id}.",
                )
                for subject_id in candidates
                if isinstance(subject_id, str) and subject_id.strip()
            ) if isinstance(candidates, list) else ()
            items.append(DecisionItem(
                item_id="subject_id",
                kind="subject_id",
                question="Which registered subject should enter the reviewed preprocessing scope?",
                options=options,
                impact=(
                    "The selected subject ID is bound into the reviewed node parameters, "
                    "Approval Summary, execution ticket hash, and output provenance."
                ),
                evidence_refs=("project:subject_count",),
            ))
        for node in plan.get("nodes", []):
            if not isinstance(node, dict):
                continue
            params = node.get("params") if isinstance(node.get("params"), dict) else {}
            native_fc = (
                node.get("id") == "native_preproc_full_execute"
                and signals.get("atlas_required")
            )
            atlas_parameter = (
                "atlas_path"
                if node.get("id") == "functional_connectivity_subject"
                else "atlas"
            )
            if (
                (node.get("id") == "functional_connectivity_subject" or native_fc)
                and not params.get(atlas_parameter)
                and "atlas" not in answers
            ):
                candidates = signals.get("atlas_candidates")
                options = tuple(
                    PendingDecisionOption(
                        id=str(item["path"]),
                        label=str(item.get("name") or item["path"]),
                        description=(
                            f"Registered resource; license={item.get('license')}; "
                            f"checksum={item.get('checksum')}"
                        ),
                    )
                    for item in candidates
                    if isinstance(item, dict)
                    and item.get("path")
                    and item.get("license")
                    and item.get("checksum")
                ) if isinstance(candidates, list) else ()
                items.append(DecisionItem(
                    item_id="atlas",
                    kind="atlas",
                    question="Which registered atlas should define functional-connectivity regions?",
                    options=options,
                    impact="The atlas changes matrix dimensions and scientific comparability.",
                    evidence_refs=("plan:functionality_connectivity_subject",),
                    answer_type="option",
                ))
        if signals.get("global_signal_regression_required") and "global_signal_regression" not in answers:
            items.append(DecisionItem(
                item_id="global_signal_regression",
                kind="global_signal_regression",
                question="Should global-signal regression be included in nuisance regression?",
                options=(
                    PendingDecisionOption(id="include", label="Include GSR", description="Regress the global mean signal."),
                    PendingDecisionOption(id="exclude", label="Exclude GSR", description="Keep the global mean signal."),
                ),
                impact="GSR changes correlation structure and can introduce negative correlations.",
                evidence_refs=("plan:science_decisions",),
            ))
        tr_conflict = signals.get("tr_conflict")
        if tr_conflict and "repetition_time" not in answers:
            options: list[PendingDecisionOption] = []
            if isinstance(tr_conflict, dict):
                labels = {"bids": "Use BIDS TR", "project": "Use project TR", "dicom": "Use DICOM TR"}
                for source, value in tr_conflict.items():
                    options.append(
                        PendingDecisionOption(
                            id=str(source),
                            label=labels.get(str(source), f"Use {source} TR"),
                            description=f"Use the {source} value ({value} s).",
                        )
                    )
            if len(options) < 2:
                options = [
                    PendingDecisionOption(id="bids", label="Use BIDS TR", description="Use the BIDS sidecar value."),
                    PendingDecisionOption(id="project", label="Use project TR", description="Use the registered project value."),
                ]
            items.append(DecisionItem(
                item_id="repetition_time",
                kind="repetition_time",
                question="Conflicting repetition-time values were detected. Which source is authoritative?",
                options=tuple(options),
                impact="TR controls slice timing, filtering, and spectral frequency interpretation.",
                evidence_refs=("plan:science_decisions",),
            ))
        if signals.get("template_required") and "template" not in answers:
            candidates = signals.get("template_candidates")
            options = tuple(
                PendingDecisionOption(
                    id=str(item["path"]),
                    label=str(item.get("name") or item["path"]),
                    description=(
                        f"Registered resource; license={item.get('license')}; "
                        f"checksum={item.get('checksum')}"
                    ),
                )
                for item in candidates
                if isinstance(item, dict)
                and item.get("path")
                and item.get("license")
                and item.get("checksum")
            ) if isinstance(candidates, list) else ()
            items.append(DecisionItem(
                item_id="template",
                kind="template",
                question="Which registered normalization template should be used?",
                options=options,
                impact="The template changes spatial correspondence and downstream comparability.",
                evidence_refs=("plan:science_decisions",),
            ))
        if signals.get("existing_run_conflict") and "overwrite" not in answers:
            items.append(DecisionItem(
                item_id="overwrite",
                kind="overwrite",
                question="A prior run already occupies the proposed output scope. How should this run proceed?",
                options=(
                    PendingDecisionOption(id="fail_if_exists", label="Stop if present", description="Preserve existing outputs and stop safely."),
                    PendingDecisionOption(id="write_new_run_directory", label="Create new run", description="Write to a distinct versioned run directory."),
                ),
                impact="Existing derivatives are never silently overwritten.",
                evidence_refs=("plan:science_decisions",),
            ))
        has_gpu = any(
            isinstance(node, dict) and str(node.get("backend") or "").lower().startswith("gpu")
            for node in plan.get("nodes", [])
        )
        if (signals.get("experimental_gpu") or signals.get("experimental_backend")) and has_gpu and "experimental_backend" not in answers:
            items.append(DecisionItem(
                item_id="experimental_backend",
                kind="experimental_backend",
                question="This plan selects an experimental GPU backend. Which reviewed backend should be used?",
                options=(
                    PendingDecisionOption(id="use_cpu", label="Use CPU", description="Use the validated CPU path."),
                    PendingDecisionOption(id="allow_experimental_gpu", label="Keep experimental GPU", description="Keep the explicitly labeled experimental backend."),
                ),
                impact="Backend selection can change precision, reproducibility, and validation status.",
                evidence_refs=("plan:backend",),
            ))
        return items

    @staticmethod
    def _dicom_conversion_decision_item(dicom_count: int) -> DecisionItem:
        return DecisionItem(
            item_id="dicom_conversion",
            kind="dicom_conversion",
            question=(
                "Approve the reviewed research DICOM conversion package before "
                "preprocessing?"
            ),
            options=(
                PendingDecisionOption(
                    id="approve_conversion",
                    label="Approve conversion preparation",
                    description=(
                        "Review all detected mappings and bind the project-local native "
                        "converter, read-only rawdata checksum, fail-if-exists outputs, "
                        "rollback plan, audit record, and research-only restrictions."
                    ),
                    recommended=True,
                ),
                PendingDecisionOption(
                    id="revise_goal",
                    label="Revise goal",
                    description=(
                        "Do not prepare conversion; revise the task goal or project input."
                    ),
                ),
            ),
            recommended_option="approve_conversion",
            impact=(
                f"The controlled package covers {dicom_count} detected DICOM files. "
                "This action persists approvals and safety evidence only; numerical "
                "conversion still requires the later Agent Approval Summary, Execution "
                "Ticket, and Execution Gateway dispatch."
            ),
            evidence_refs=("project:dicom_file_count", "project:rawdata_read_only"),
        )

    @staticmethod
    def _memory_decision_items(
        memory_context: MemoryContext | None,
        command_context: dict[str, Any],
    ) -> list[DecisionItem]:
        if memory_context is None:
            return []
        answers = dict(command_context.get("science_answers") or {})
        ignored = set(command_context.get("ignored_memory_ids") or [])
        items: list[DecisionItem] = []
        for suggestion in memory_context.decision_suggestions:
            if suggestion.memory_id in ignored or suggestion.decision_kind in answers:
                continue
            value = suggestion.typed_value.get("value")
            value_id = str(value).casefold() if isinstance(value, bool) else str(value)
            items.append(DecisionItem(
                item_id=f"memory_{suggestion.decision_kind}_{suggestion.memory_id}",
                kind=(
                    suggestion.decision_kind
                    if suggestion.decision_kind
                    in {
                        "atlas",
                        "global_signal_regression",
                        "repetition_time",
                        "template",
                        "overwrite",
                        "experimental_backend",
                    }
                    else "other"
                ),
                question="Use this previously confirmed project decision for the current task?",
                options=(
                    PendingDecisionOption(
                        id=value_id,
                        label=f"Use {value_id}",
                        description="Confirm the remembered value for this Agent Task only.",
                        recommended=True,
                    ),
                    PendingDecisionOption(
                        id="__ignore_memory__",
                        label="Do not use memory",
                        description="Ignore this suggestion for the current Agent Task.",
                    ),
                ),
                recommended_option=value_id,
                impact="Scientific memory is advisory and requires confirmation for every Agent Task.",
                source="memory_suggestion",
                memory_id=suggestion.memory_id,
                recommendation_source=f"memory:{suggestion.memory_id}",
                evidence_refs=tuple(suggestion.source_refs),
            ))
        return items

    def _decision_batch(
        self, plan: dict[str, Any], context: dict[str, Any], metadata: dict[str, Any],
        evidence_snapshot_hash: str, memory_context: MemoryContext | None, lifecycle,
    ) -> PendingDecisionBatch | None:
        items = [
            *self._memory_decision_items(memory_context, context),
            *self._science_decision_items(plan, context, metadata),
        ]
        # A real project value resolves a known input; it never resolves a science choice.
        requested = {item.kind for item in items}
        if len(items) > 6:
            return self._goal_revision_batch(
                lifecycle, evidence_snapshot_hash,
                "More than six required decisions indicate an ambiguous goal or project state.",
            )
        if not items:
            return None
        return PendingDecisionBatch(
            batch_id=f"decision_batch_{uuid4().hex}", lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id, evidence_snapshot_hash=evidence_snapshot_hash,
            plan_hash_before=stable_hash(plan), items=tuple(items),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            source="memory_suggestion" if requested and all(item.source == "memory_suggestion" for item in items) else "planner",
        )

    @staticmethod
    def _memory_context(context: dict[str, Any]) -> MemoryContext | None:
        raw = context.get("memory_context") if isinstance(context, dict) else None
        return MemoryContext.model_validate(raw) if isinstance(raw, dict) else None

    @staticmethod
    def _goal_revision_batch(lifecycle, evidence_snapshot_hash: str, reason: str) -> PendingDecisionBatch:
        return PendingDecisionBatch(
            batch_id=f"decision_batch_{uuid4().hex}", lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id, evidence_snapshot_hash=evidence_snapshot_hash,
            items=(DecisionItem(
                item_id="goal_revision", kind="goal_revision", question="Revise the research goal to match a supported workflow.",
                impact=reason, answer_type="text", evidence_refs=("evidence:decision_limit",),
            ),),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )

    def _conversion_readiness(self, plan: dict[str, Any]) -> dict[str, Any]:
        for node in plan.get("nodes", []):
            if not isinstance(node, dict) or node.get("id") != self.conversion_node_id:
                continue
            params = node.get("params") if isinstance(node.get("params"), dict) else {}
            return self.conversion_checker(
                project_id=str(params.get("project_id") or ""),
                conversion_run_id=str(params.get("conversion_run_id") or ""),
                project_dir=str(params.get("project_dir") or ""),
                rawdata_dir=str(params.get("rawdata_dir") or ""),
                output_dir=str(params.get("output_dir") or ""),
            )
        return {"ok": True, "status": "not_required"}

    def _command_replay(self, project_id: str, command_id: str):
        for lifecycle in self.store.list_agent_lifecycles(project_id):
            for event in self.store.list_agent_lifecycle_events(lifecycle.lifecycle_id):
                if event.command_id == command_id:
                    return self.store.get_agent_lifecycle(lifecycle.lifecycle_id)
        return None
