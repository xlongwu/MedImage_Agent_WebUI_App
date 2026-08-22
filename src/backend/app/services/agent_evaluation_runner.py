"""Isolated, deterministic execution of the version-two Agent evaluation suite."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from src.backend.app.core.config_schema import AgentHarnessConfig, AgentModelRuntimeConfig, MemoryConfig
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.agent_model_adapter import (
    ActionCallMetadata,
    ActionProposal,
    AgentModelAdapter,
    AgentModelInvalidOutputError,
    AgentModelProviderError,
)
from src.backend.app.planner.memory_influence_guard import MemoryInfluenceError, MemoryInfluenceGuard
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.agent_eval import (
    AgentEvalCase,
    AgentEvalCaseResult,
    AgentEvalManifest,
    AgentEvalOutcome,
    AgentEvaluationReport,
)
from src.backend.app.schemas.agent_harness import DraftPlanAction, RequestDecisionAction
from src.backend.app.schemas.agent_lifecycle import DecisionItem, PendingDecisionOption
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_approval_execution_service import AgentApprovalExecutionService
from src.backend.app.services.agent_evaluation_service import AgentEvaluationService
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_harness_context_service import (
    AgentContextLimitExceededError,
    HarnessContextBuilder,
    HarnessContextSources,
)
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.agent_recovery_command_service import AgentRecoveryCommandService
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.agent_trace_service import AgentTraceService
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.services.memory_consolidation_service import MemoryConsolidationService
from src.backend.app.services.memory_management_service import MemoryManagementService
from src.backend.app.services.memory_repository import MemoryRepository
from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _tree_hash(root: Path) -> str:
    digest = sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


class _FixedActionAdapter:
    def __init__(self, action) -> None:
        self.action = action

    def propose_action(self, *, request) -> ActionProposal:
        return ActionProposal.rule_based(self.action)


class _UnavailableAdapter:
    def propose_action(self, *, request) -> ActionProposal:
        raise AgentModelProviderError(
            "AGENT_HARNESS_PROVIDER_UNAVAILABLE",
            ActionCallMetadata(
                provider=request.provider,
                model=request.model,
                endpoint_class=request.endpoint_class,
                response_hash=None,
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
                latency_ms=None,
                provider_request_id=None,
                network_called=False,
            ),
        )


class _ProviderErrorAdapter:
    def __init__(self, code: str, *, network_called: bool) -> None:
        self.code = code
        self.network_called = network_called

    def propose_action(self, *, request) -> ActionProposal:
        raise AgentModelProviderError(
            self.code,
            ActionCallMetadata(
                provider=request.provider,
                model=request.model,
                endpoint_class=request.endpoint_class,
                response_hash=None,
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
                latency_ms=1 if self.network_called else None,
                provider_request_id=None,
                network_called=self.network_called,
            ),
        )


class _InvalidOutputAdapter:
    def __init__(self, *, repair_succeeds: bool = False) -> None:
        self.calls = 0
        self.repair_succeeds = repair_succeeds

    def propose_action(self, *, request) -> ActionProposal:
        self.calls += 1
        if self.repair_succeeds and self.calls == 2:
            return ActionProposal.rule_based(DraftPlanAction(
                kind="draft_plan",
                reason="Repaired typed action.",
                expected_state="CREATED",
            ))
        raise AgentModelInvalidOutputError(
            "AGENT_HARNESS_MODEL_OUTPUT_INVALID",
            ActionCallMetadata(
                provider=request.provider,
                model=request.model,
                endpoint_class=request.endpoint_class,
                response_hash=_hash(f"invalid-output:{self.calls}"),
                input_tokens=None,
                output_tokens=None,
                cached_input_tokens=None,
                latency_ms=1,
                provider_request_id=None,
                network_called=True,
            ),
        )


class _SimulatedProcessLoss(BaseException):
    pass


class _UnknownOutcomeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def propose_action(self, *, request) -> ActionProposal:
        self.calls += 1
        raise _SimulatedProcessLoss("simulated after persisted call start")


class _NoProbeRepository:
    def __getattr__(self, name: str):
        raise AssertionError(f"disabled Memory unexpectedly accessed {name}")


class AgentEvaluationRunner:
    """Run fixed drivers through real isolated lifecycle and projection services."""

    def run_manifest(
        self, *, manifest: AgentEvalManifest, model_adapter: AgentModelAdapter,
    ) -> AgentEvaluationReport:
        outcomes: list[AgentEvalOutcome] = []
        results: list[AgentEvalCaseResult] = []
        profiles: set[str] = set()
        for case in manifest.cases:
            outcome, result, profile = self._run_case(case, model_adapter)
            outcomes.append(outcome)
            results.append(result)
            profiles.add(profile)
        profile_hash = next(iter(profiles)) if len(profiles) == 1 else _hash("|".join(sorted(profiles)))
        return AgentEvaluationService().evaluate(
            manifest=manifest,
            outcomes=outcomes,
            results=tuple(results),
            model_profile_hash=profile_hash,
        )

    def _run_case(
        self, case: AgentEvalCase, model_adapter: AgentModelAdapter,
    ) -> tuple[AgentEvalOutcome, AgentEvalCaseResult, str]:
        with TemporaryDirectory(prefix="medimage-agent-eval-") as root_value:
            root = Path(root_value)
            store = SQLiteDesktopStore(root / "state.sqlite")
            project_id = f"eval-{_hash(case.case_id)[:12]}"
            rawdata = root / "rawdata"
            rawdata.mkdir()
            rawdata_before = _tree_hash(rawdata)
            store.add_project(
                ProjectDetail(
                    id=project_id,
                    name="evaluation",
                    study_id="synthetic",
                    modality="rs-fMRI",
                    created_date="synthetic",
                    subjects_count=0,
                    current_pipeline_id="evaluation",
                    sequences=[],
                    scans_count=0,
                    total_size="0",
                    current_model_id="rule_based",
                    metadata={
                        "project_dir": str(root),
                        "project_config_path": str(
                            Path("examples/project_config_synthetic_smoke.yaml").resolve()
                        ),
                    },
                ),
                health_status="ready",
                rawdata_dir=str(rawdata),
            )
            if case.driver.startswith("memory_"):
                store.set_memory_consent(
                    project_id=project_id,
                    command_id=f"eval:{case.case_id}:memory-consent",
                    principal="evaluation",
                    generate_enabled=True,
                    use_enabled=True,
                )
            adapter = self._adapter_for_case(case, model_adapter)
            service, scheduler = self._build_service(store, adapter)
            lifecycle = service.create(
                project_id=project_id,
                goal=case.goal,
                command_id=f"eval:{case.case_id}:create",
                actor="evaluation",
            )
            lifecycle_count = len(store.list_agent_lifecycles(project_id))
            replay_idempotent = True
            if case.driver == "duplicate_command":
                replay = service.create(
                    project_id=project_id,
                    goal=case.goal,
                    command_id=f"eval:{case.case_id}:create",
                    actor="evaluation",
                )
                replay_idempotent = replay.lifecycle_id == lifecycle.lifecycle_id
            if case.driver == "restart_recovery":
                scheduler.shutdown()
                service, scheduler = self._build_service(store, adapter)
                scheduler.rescan()
            if case.driver == "unknown_call_outcome":
                owner = f"eval:{case.case_id}"
                try:
                    scheduler.run_once(owner=owner)
                except _SimulatedProcessLoss:
                    wake = next(
                        item
                        for item in store.list_agent_task_wakes(
                            project_id=project_id, include_consumed=True
                        )
                        if item.status == "CLAIMED"
                    )
                    now = datetime.now(UTC)
                    store.retry_agent_task_wake(
                        wake,
                        owner=owner,
                        now=now,
                        available_at=now,
                        error_code="AGENT_EVAL_SIMULATED_PROCESS_LOSS",
                    )
                    interrupted = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
                    store.update_agent_harness_attempt(
                        interrupted.model_copy(
                            update={"lease_expires_at": now - timedelta(seconds=1)}
                        ),
                        expected_status="RUNNING",
                        expected_step_no=interrupted.next_step_no,
                        expected_lease_owner=interrupted.lease_owner,
                    )
                    scheduler.shutdown()
                    service, scheduler = self._build_service(store, adapter)
            for _ in range(8):
                if scheduler.run_once(owner=f"eval:{case.case_id}") is None:
                    break
            scheduler.shutdown()

            lifecycle = store.get_agent_lifecycle(lifecycle.lifecycle_id)
            attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            steps = store.list_agent_harness_steps(attempt.attempt_id) if attempt else []
            calls = [call for step in steps for call in step.model_calls]
            actions = store.list_agent_harness_actions(attempt.attempt_id) if attempt else []
            contexts = [
                store.get_agent_harness_context(attempt.context_hash)
            ] if attempt and attempt.context_hash else []
            contexts = [item for item in contexts if item is not None]
            tickets = store.list_execution_tickets(project_id)
            run_links = store.list_run_links(project_id)
            forbidden = tuple(
                name
                for name, present in (
                    ("execution_ticket", bool(tickets)),
                    ("gateway_or_run", bool(run_links) or lifecycle.run_id is not None),
                )
                if present
            )
            if _tree_hash(rawdata) != rawdata_before:
                forbidden = (*forbidden, "rawdata_modified")
            duplicate_observed = (
                not replay_idempotent
                or len(store.list_agent_lifecycles(project_id)) != lifecycle_count
                or len({item.action_id for item in actions}) != len(actions)
                or len({item.execution_ticket_id for item in tickets}) != len(tickets)
                or (case.driver == "unknown_call_outcome" and len(calls) != 1)
            )
            probes = self._driver_probe(case, root, store, project_id, lifecycle)
            rejected_codes = {
                item.error_code for item in (*steps, *actions) if item.error_code
            }
            context_complete = all(item.complete for item in contexts) and bool(contexts)
            context_complete = probes.get("context_required_sections_complete", context_complete)
            outcome = AgentEvalOutcome(
                case_id=case.case_id,
                route_correct=lifecycle.state == case.expected_final_state,
                necessary_question_asked=(
                    lifecycle.pending_decision_batch is not None
                    if case.driver == "decision_required"
                    else None
                ),
                reached_expected_stop=lifecycle.state == case.expected_stop_point,
                unsafe_action_rejected=(
                    bool(rejected_codes) and not forbidden
                    if case.driver in {
                        "invalid_action",
                        "invalid_json",
                        "invalid_action_type",
                        "unsafe_path",
                    }
                    else None
                ),
                stale_or_cross_project_blocked=probes.get("stale_or_cross_project_blocked"),
                plan_only_zero_execution=(
                    not forbidden and lifecycle.execution_ticket_id is None and lifecycle.run_id is None
                    if case.driver in {"plan_only", "repair_then_valid"}
                    else None
                ),
                duplicate_side_effect_observed=(
                    duplicate_observed
                    if case.driver in {
                        "duplicate_command",
                        "restart_recovery",
                        "unknown_call_outcome",
                    }
                    else None
                ),
                schema_repaired=(
                    bool(actions)
                    and any(call.repair for call in calls)
                    and calls[-1].status == "succeeded"
                    if case.driver == "repair_then_valid"
                    else None
                ),
                fallback_used=(
                    bool(attempt and attempt.fallback_to)
                    if case.driver in {
                        "provider_failure",
                        "provider_timeout",
                        "missing_api_key",
                    }
                    else None
                ),
                step_count=len(steps),
                model_call_count=sum(item.network_called for item in calls),
                latency_ms=sum(item.latency_ms or 0 for item in calls),
                user_interactions=int(
                    lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"}
                ),
                memory_relevant_included=probes.get("memory_relevant_included"),
                memory_irrelevant_excluded=probes.get("memory_irrelevant_excluded"),
                memory_stale_blocked=probes.get("memory_stale_blocked"),
                memory_science_confirmation_required=probes.get(
                    "memory_science_confirmation_required"
                ),
                context_required_sections_complete=bool(context_complete),
                context_cross_project_blocked=probes.get("context_cross_project_blocked"),
            )
            failures: list[str] = []
            if outcome.route_correct is not True:
                failures.append("AGENT_EVAL_FINAL_STATE_MISMATCH")
            if outcome.reached_expected_stop is not True:
                failures.append("AGENT_EVAL_STOP_POINT_MISMATCH")
            if bool(tickets or run_links) != case.expect_execution:
                failures.append("AGENT_EVAL_EXECUTION_MISMATCH")
            if forbidden:
                failures.append("AGENT_EVAL_FORBIDDEN_SIDE_EFFECT")
            if duplicate_observed:
                failures.append("AGENT_EVAL_DUPLICATE_COMMAND_SIDE_EFFECT")
            for field, expected in case.required_outcomes.items():
                if getattr(outcome, field, None) is not expected:
                    failures.append(f"AGENT_EVAL_REQUIRED_OUTCOME_MISMATCH:{field}")
            trace = AgentTraceService(store).get(
                project_id=project_id, lifecycle_id=lifecycle.lifecycle_id
            )
            evidence_hashes = tuple(sorted({
                value
                for value in (
                    lifecycle.evidence_snapshot_hash,
                    lifecycle.goal_contract_hash,
                    trace.integrity_hash,
                )
                if value
            }))
            profile = next(
                (item.model_profile_hash for item in calls if item.model_profile_hash),
                _hash(type(adapter).__name__),
            )
            result = AgentEvalCaseResult(
                case_id=case.case_id,
                passed=not failures,
                final_state=lifecycle.state,
                observed_stop_point=lifecycle.state,
                action_kinds=tuple(item.kind for item in actions),
                forbidden_calls_observed=forbidden,
                failure_codes=tuple(failures),
                lifecycle_id_hash=_hash(lifecycle.lifecycle_id),
                trace_hash=trace.integrity_hash,
                evidence_hashes=evidence_hashes,
                outcome=outcome,
            )
            return outcome, result, profile

    @staticmethod
    def _adapter_for_case(case: AgentEvalCase, default: AgentModelAdapter) -> AgentModelAdapter:
        if case.driver == "provider_failure":
            return _UnavailableAdapter()
        if case.driver in {"invalid_json", "invalid_action_type"}:
            return _InvalidOutputAdapter()
        if case.driver == "repair_then_valid":
            return _InvalidOutputAdapter(repair_succeeds=True)
        if case.driver == "provider_timeout":
            return _ProviderErrorAdapter(
                "AGENT_MODEL_PROVIDER_TIMEOUT", network_called=True
            )
        if case.driver == "missing_api_key":
            return _ProviderErrorAdapter(
                "AGENT_MODEL_CONFIG_INCOMPLETE", network_called=False
            )
        if case.driver == "unknown_call_outcome":
            return _UnknownOutcomeAdapter()
        if case.driver == "decision_required":
            return _FixedActionAdapter(RequestDecisionAction(
                kind="request_decision",
                reason="A registered prerequisite must be supplied.",
                expected_state="CREATED",
                input_refs=("goal", "project_evidence"),
                decision=DecisionItem(
                    item_id="eval-input",
                    kind="missing_input",
                    question="Provide the registered project input.",
                    impact="Planning cannot continue without registered evidence.",
                    options=(PendingDecisionOption(
                        id="register",
                        label="Register input",
                        description="Register synthetic input.",
                    ),),
                    recommended_option="register",
                ),
            ))
        if case.driver == "invalid_action":
            return _FixedActionAdapter(DraftPlanAction(
                kind="draft_plan", reason="Invalid stale action", expected_state="APPROVED",
            ))
        if case.driver == "approval_drift":
            return _FixedActionAdapter(DraftPlanAction(
                kind="draft_plan", reason="Stale model identity", expected_state="PLAN_DRAFTED",
            ))
        if case.driver == "unsafe_path":
            return _FixedActionAdapter(DraftPlanAction(
                kind="draft_plan",
                reason="Unregistered input",
                expected_state="CREATED",
                input_refs=("unregistered_path",),
            ))
        return default

    def _driver_probe(self, case, root, store, project_id, lifecycle) -> dict[str, bool]:
        if case.driver.startswith("memory_"):
            return self._memory_probe(case.driver, root, store, project_id)
        if case.driver.startswith("context_"):
            return self._context_probe(case.driver, store, project_id, lifecycle)
        if case.driver == "approval_drift":
            attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            steps = store.list_agent_harness_steps(attempt.attempt_id) if attempt else []
            return {"stale_or_cross_project_blocked": any(
                item.error_code == "AGENT_HARNESS_STALE_ACTION" for item in steps
            )}
        return {}

    @staticmethod
    def _memory_probe(driver: str, root: Path, store, project_id: str) -> dict[str, bool]:
        if driver == "memory_disabled_zero_probe":
            result = MemoryRetrievalService(
                repository=_NoProbeRepository(),
                project_store=store,
                config=MemoryConfig(
                    enabled=False,
                    generation_enabled=False,
                    use_enabled=False,
                    store_path=str(root / "disabled-memory.sqlite"),
                ),
            ).retrieve(project_id=project_id, query="disabled")
            return {"memory_irrelevant_excluded": result.status == "disabled" and not result.items}

        config = MemoryConfig(
            enabled=True,
            generation_enabled=True,
            use_enabled=True,
            store_path=str(root / "memory.sqlite"),
        )
        repository = MemoryRepository(config.store_path)
        manager = MemoryManagementService(
            project_store=store, memory_repository=repository, config=config
        )
        retrieval = MemoryRetrievalService(
            repository=repository, project_store=store, config=config
        )
        if driver == "memory_partial_health":
            health = retrieval.operational_health(project_id=project_id)
            return {"memory_irrelevant_excluded": health["status"] == "partial"}
        if driver == "memory_science_confirmation_required":
            remembered = manager.remember(
                project_id=project_id,
                command_id="eval-memory-science-remember",
                principal="evaluation",
                kind="project_decision",
                key="atlas",
                value={"decision_kind": "atlas", "value": "schaefer-200"},
                summary="Synthetic atlas decision.",
                impact_class="scientific",
            )
            candidate = repository.get_candidate(
                project_id=project_id, candidate_id=remembered["candidate_id"]
            )
            manager.review_candidate(
                project_id=project_id,
                candidate_id=candidate.candidate_id,
                command_id="eval-memory-science-accept",
                principal="evaluation",
                accept=True,
                expected_candidate_version=candidate.candidate_version,
                candidate_hash=candidate.candidate_hash,
            )
            MemoryConsolidationService(
                project_store=store, memory_repository=repository
            ).consolidate_project(project_id=project_id)
            context = retrieval.build_context(project_id=project_id, goal="atlas connectivity")
            plan = {"nodes": [{
                "id": "native_preproc_full_execute",
                "backend": "python-cpu",
                "params": {"atlas": "schaefer-200"},
            }]}
            try:
                MemoryInfluenceGuard().validate(plan=plan, memory_context=context)
            except MemoryInfluenceError as exc:
                blocked = exc.code == "MEMORY_SCIENTIFIC_CONFIRMATION_REQUIRED"
            else:
                blocked = False
            return {"memory_science_confirmation_required": blocked}

        if driver == "memory_stale_authoritative_source":
            remembered = manager.remember(
                project_id=project_id,
                command_id="eval-memory-stale-remember",
                principal="evaluation",
                kind="project_decision",
                key="atlas",
                value={"decision_kind": "atlas", "value": "schaefer-200"},
                summary="Synthetic atlas decision for staleness verification.",
                impact_class="scientific",
            )
            candidate = repository.get_candidate(
                project_id=project_id, candidate_id=remembered["candidate_id"]
            )
            manager.review_candidate(
                project_id=project_id,
                candidate_id=candidate.candidate_id,
                command_id="eval-memory-stale-accept",
                principal="evaluation",
                accept=True,
                expected_candidate_version=candidate.candidate_version,
                candidate_hash=candidate.candidate_hash,
            )
            MemoryConsolidationService(
                project_store=store, memory_repository=repository
            ).consolidate_project(project_id=project_id)
            item = repository.list_items(project_id=project_id)[0]
            with repository.connect(immediate=True) as conn:
                conn.execute(
                    "UPDATE memory_sources SET source_trust_class='authoritative_structured', "
                    "source_type='observation', source_id='missing' WHERE memory_id=?",
                    (item.memory_id,),
                )
            result = retrieval.retrieve(project_id=project_id, query="atlas")
            return {"memory_stale_blocked": not result.items and result.omitted_count > 0}

        manager.remember(
            project_id=project_id,
            command_id=f"eval-{driver}-remember",
            principal="evaluation",
            kind="user_preference",
            key="report-language",
            value={"language": "zh-CN"},
            summary="Prefer Chinese reports.",
            impact_class="presentation",
        )
        if driver == "memory_irrelevant_preference":
            result = retrieval.retrieve(project_id=project_id, query="head motion threshold")
            return {"memory_irrelevant_excluded": not result.items}
        result = retrieval.retrieve(project_id=project_id, query="Chinese report language")
        return {"memory_relevant_included": bool(result.items)}

    @staticmethod
    def _context_probe(driver: str, store, project_id: str, lifecycle) -> dict[str, bool]:
        project = store.get_project(project_id)
        evidence = AgentEvidenceService(store).build_snapshot(
            project_id=project_id, lifecycle_id=lifecycle.lifecycle_id
        )
        builder = HarnessContextBuilder()
        if driver == "context_cross_project_reference":
            try:
                AgentEvidenceService(store).read_for_context(
                    snapshot_hash=evidence.snapshot_hash,
                    project_id="other-project",
                    lifecycle_id=lifecycle.lifecycle_id,
                    purpose="plan_draft",
                )
            except SafetyError:
                blocked = True
            else:
                blocked = False
            return {
                "context_cross_project_blocked": blocked,
                "stale_or_cross_project_blocked": blocked,
                "context_required_sections_complete": blocked,
            }
        if driver == "context_required_section_missing":
            context = builder.build(sources=HarnessContextSources(
                lifecycle=lifecycle.model_copy(update={"goal_text": ""}),
                project=project,
                evidence_snapshot=evidence,
            ))
            return {"context_required_sections_complete": not context.complete}
        if driver == "context_optional_section_omitted":
            large = lifecycle.model_copy(update={"command_context": {
                **(lifecycle.command_context or {}),
                "memory_context": {
                    "memory_ids": ["m1"],
                    "decision_suggestions": [{"summary": "x" * 2048} for _ in range(24)],
                    "planner_constraints": {
                        f"constraint-{index}": "x" * 2048 for index in range(24)
                    },
                },
            }})
            context = builder.build(sources=HarnessContextSources(
                lifecycle=large, project=project, evidence_snapshot=evidence,
            ))
            return {"context_required_sections_complete": (
                context.complete
                and any(item.startswith("memory_context:") for item in context.omitted_sections)
            )}
        builder.MAX_BYTES = 32
        try:
            builder.build(sources=HarnessContextSources(
                lifecycle=lifecycle, project=project, evidence_snapshot=evidence,
            ))
        except AgentContextLimitExceededError:
            blocked = True
        else:
            blocked = False
        return {"context_required_sections_complete": blocked}

    @staticmethod
    def _build_service(store, model_adapter: AgentModelAdapter):
        model_config = AgentModelRuntimeConfig()
        harness = AgentHarnessService(
            store,
            config=AgentHarnessConfig(enabled=True),
            model_config=model_config,
            adapter=model_adapter,
        )
        planning = AgentPlanningService(
            store,
            planner=None,
            goal_planning_service=GoalPlanningService(),
            plan_saver=save_reviewed_plan,
            summary_service=ApprovalSummaryService(),
            conversion_checker=ReviewedConversionService().check_readiness,
            conversion_node_id=ReviewedConversionService.NODE_ID,
            memory_influence_guard=MemoryInfluenceGuard(),
            harness_config=harness.config,
            model_config=model_config,
            harness_service=harness,
            evidence_service=AgentEvidenceService(store),
        )
        scheduler = AgentTaskScheduler(store, planning_service=planning, start_workers=False)
        planning.bind_scheduler(scheduler)
        reconciler = AgentTaskReconciler(store)
        approval = AgentApprovalExecutionService(
            store,
            executor=ReviewedExecutionService(),
            summary_service=ApprovalSummaryService(),
            dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
            reconcile_once=reconciler.reconcile_once,
            monitor_scheduler=reconciler.start_bounded_monitor,
        )
        command = AgentTaskCommandService(
            store,
            planning_service=planning,
            approval_execution_service=approval,
            recovery_command_service=AgentRecoveryCommandService(
                store,
                stop_planning=harness.stop,
                recovery_execution_factory=RecoveryExecutionService,
            ),
        )
        return command, scheduler
