"""Durable, lease-fenced scheduler for fixed-role planning reviews."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from threading import Condition, Thread
from typing import Callable
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_team import (
    AgentTeamEvent,
    AgentTeamFinding,
    AgentTeamModelCallRecord,
    AgentTeamRecord,
    AgentTeamWakeRecord,
    AgentTeamWorkItem,
    agent_team_finding_hash,
    canonical_hash,
)
from src.backend.app.services.agent_multi_agent_policy_service import AgentMultiAgentPolicyService
from src.backend.app.services.agent_team_coordinator_service import (
    AgentTeamCoordinatorError,
    AgentTeamCoordinatorService,
)
from src.backend.app.services.agent_team_worker_service import (
    AgentTeamWorkerError,
    AgentTeamWorkerService,
)
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService

logger = logging.getLogger(__name__)


class _RetryTeamWake(RuntimeError):
    def __init__(self, available_at: datetime) -> None:
        super().__init__("TEAM_SCHEDULER_FAILED")
        self.code = "TEAM_SCHEDULER_FAILED"
        self.available_at = available_at


_REPAIRABLE_OUTPUT_ERRORS = frozenset({
    "MULTI_AGENT_REVIEW_OUTPUT_INVALID",
    "MULTI_AGENT_REVIEWER_KIND_MISMATCH",
    "MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN",
    "MULTI_AGENT_REVIEW_INPUT_REF_INVALID",
    "MULTI_AGENT_REVIEW_FORBIDDEN_SUGGESTION",
})
_TERMINAL_WORK_STATUSES = frozenset({"COMPLETED", "FAILED", "INDETERMINATE", "CANCELED"})
_PERMANENT_WAKE_ERRORS = frozenset({
    "MULTI_AGENT_DISABLED",
    "MULTI_AGENT_CONSENT_INVALIDATED",
    "MULTI_AGENT_BUDGET_EXCEEDED",
    "AGENT_MODEL_CONFIG_INCOMPLETE",
    "AGENT_MODEL_CONFIG_INVALID",
    "AGENT_TEAM_PROVIDER_UNAVAILABLE",
    "AGENT_TEAM_CONTEXT_BINDING_INVALID",
    "AGENT_TEAM_PLANNING_STATE_INVALID",
    "TEAM_ROLE_REGISTRY_INVALID",
    "TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED",
})


class AgentTeamScheduler:
    """Claim durable Team wakes and execute at most three independent reviews."""

    LEASE_SECONDS = 30
    MAX_RETRY_DELAY_SECONDS = 30
    MAX_WAKE_ATTEMPTS = 5
    RESCAN_LIMIT = 100
    MAX_CONCURRENCY = 3

    def __init__(
        self,
        store,
        *,
        worker_service: AgentTeamWorkerService,
        coordinator_service: AgentTeamCoordinatorService,
        policy_service: AgentMultiAgentPolicyService,
        planning_resume: Callable[[str, str], object] | None = None,
        start_workers: bool = True,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.worker_service = worker_service
        self.coordinator_service = coordinator_service
        self.policy_service = policy_service
        self.planning_resume = planning_resume
        self.start_workers = start_workers
        self.now = now or (lambda: datetime.now(UTC))
        self._accepting = True
        self._condition = Condition()
        self._workers: list[Thread] = []

    def notify(self) -> None:
        with self._condition:
            self._condition.notify_all()
        if self._accepting and self.start_workers:
            self._start_workers()

    def claim_next(self, *, owner: str) -> AgentTeamWakeRecord | None:
        now = self.now()
        wake = self.store.claim_next_agent_team_wake(
            owner=owner,
            now=now,
            lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS),
        )
        if wake is not None:
            logger.info(
                "agent_team_wake_claimed",
                extra={
                    "team_id": wake.team_id,
                    "lifecycle_id": wake.lifecycle_id,
                    "event_code": (
                        "AGENT_TEAM_WAKE_RECLAIMED"
                        if wake.attempt_count > 1
                        else "AGENT_TEAM_WAKE_CLAIMED"
                    ),
                },
            )
        return wake

    def run_once(self, *, owner: str | None = None) -> str | None:
        """Claim and process exactly one persisted Team checkpoint."""
        if not self._accepting:
            return None
        owner = owner or f"agent-team-scheduler:{uuid4().hex}"
        wake = self.claim_next(owner=owner)
        if wake is None:
            return None
        try:
            if wake.reason == "work_item":
                self._run_work_item(wake, owner=owner)
            elif wake.reason == "aggregate":
                self._run_aggregation(wake)
            elif wake.reason == "planning_resume":
                if self.planning_resume is None:
                    raise AgentTeamCoordinatorError("AGENT_TEAM_PLANNING_STATE_INVALID")
                self.planning_resume(wake.project_id, wake.lifecycle_id)
            self.store.complete_agent_team_wake(wake, owner=owner, now=self.now())
            with self._condition:
                self._condition.notify_all()
            return wake.team_id
        except Exception as exc:
            if self._lost_wake_lease(exc):
                return wake.team_id
            now = self.now()
            team = self.store.get_agent_team(wake.project_id, wake.team_id)
            if team is None or team.status in {
                "COMPLETED", "NEEDS_ATTENTION", "CANCELED", "FAILED"
            }:
                try:
                    self.store.complete_agent_team_wake(wake, owner=owner, now=now)
                except RuntimeError:
                    pass
                return wake.team_id
            if isinstance(exc, _RetryTeamWake):
                self.store.retry_agent_team_wake(
                    wake,
                    owner=owner,
                    now=now,
                    available_at=max(now, exc.available_at),
                    error_code="TEAM_SCHEDULER_FAILED",
                )
                return wake.team_id
            error_code = getattr(exc, "code", None) or "TEAM_SCHEDULER_FAILED"
            if error_code in _PERMANENT_WAKE_ERRORS:
                self.store.fail_agent_team_wake(
                    wake,
                    owner=owner,
                    now=now,
                    error_code=error_code,
                )
                return wake.team_id
            if wake.attempt_count >= self.MAX_WAKE_ATTEMPTS:
                self.store.fail_agent_team_wake(
                    wake,
                    owner=owner,
                    now=now,
                    error_code="TEAM_SCHEDULER_FAILED",
                )
                return wake.team_id
            if error_code not in {
                "MULTI_AGENT_DISABLED",
                "MULTI_AGENT_CONSENT_INVALIDATED",
                "MULTI_AGENT_BUDGET_EXCEEDED",
                "AGENT_MODEL_CONFIG_INCOMPLETE",
                "AGENT_MODEL_CONFIG_INVALID",
                "AGENT_MODEL_PROVIDER_FAILED",
                "AGENT_MODEL_PROVIDER_TIMEOUT",
                "AGENT_TEAM_PROVIDER_UNAVAILABLE",
                "AGENT_TEAM_CONTEXT_BINDING_INVALID",
                "AGENT_TEAM_PLANNING_STATE_INVALID",
                "TEAM_ROLE_REGISTRY_INVALID",
                "TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED",
                "TEAM_SCHEDULER_FAILED",
            }:
                error_code = "TEAM_SCHEDULER_FAILED"
            delay = min(2 ** min(wake.attempt_count, 5), self.MAX_RETRY_DELAY_SECONDS)
            self.store.retry_agent_team_wake(
                wake,
                owner=owner,
                now=now,
                available_at=now + timedelta(seconds=delay),
                error_code=error_code,
            )
            logger.warning(
                "agent_team_wake_retry_scheduled",
                extra={
                    "team_id": wake.team_id,
                    "lifecycle_id": wake.lifecycle_id,
                    "event_code": "AGENT_TEAM_WAKE_RETRY_SCHEDULED",
                },
            )
            return wake.team_id

    def recover_once_on_startup(self) -> tuple[str, ...]:
        """Drain a bounded page of due durable wakes after application startup."""
        recovered: list[str] = []
        for _ in range(self.RESCAN_LIMIT):
            team_id = self.run_once(owner="agent-team-startup")
            if team_id is None:
                break
            recovered.append(team_id)
        return tuple(recovered)

    def shutdown(self) -> bool:
        with self._condition:
            self._accepting = False
            self._condition.notify_all()
            workers = tuple(self._workers)
        for worker in workers:
            if worker.is_alive():
                worker.join(timeout=self.LEASE_SECONDS)
        return all(not worker.is_alive() for worker in workers)

    def _run_work_item(self, wake: AgentTeamWakeRecord, *, owner: str) -> None:
        team = self.store.get_agent_team(wake.project_id, wake.team_id)
        if team is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        work_item_id = self._work_item_id(wake.step_key)
        work = self.store.get_agent_team_work_item(wake.project_id, work_item_id)
        if (
            work is None
            or work.team_id != team.team_id
            or work.attempt_no != self._work_attempt_no(wake.step_key)
        ):
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        if work.status in _TERMINAL_WORK_STATUSES:
            return
        context_pair = self.store.get_agent_team_context(wake.project_id, wake.team_id)
        if context_pair is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        snapshot, role_contexts = context_pair
        role_context = role_contexts.get(work.reviewer_kind)
        if role_context is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        consent = ProjectAgentSettingsService(self.store).get(
            project_id=wake.project_id
        ).multi_agent_consent

        if work.status == "MODEL_STARTED":
            self._recover_started_call(
                team=team,
                work=work,
                snapshot=snapshot,
                role_context=role_context,
                consent=consent,
                wake=wake,
                owner=owner,
            )
            return
        if work.status not in {"PENDING", "RETRY_READY"}:
            return
        repair_of = work.model_call_id if work.attempt_no == 2 else None
        prepared = self.worker_service.prepare_review(
            team=team,
            work_item=work,
            context_snapshot=snapshot,
            role_context=role_context,
            consent=consent,
            repair_of_model_call_id=repair_of,
            now=self.now(),
        )
        call = AgentTeamModelCallRecord(
            model_call_id=f"team_call_{uuid4().hex}",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            work_item_id=work.work_item_id,
            reviewer_kind=work.reviewer_kind,
            role_id=work.role_id,
            role_registry_hash=team.role_registry_hash,
            role_context_hash=role_context.role_context_hash,
            model_profile_hash=team.model_profile_hash,
            request_hash=prepared.request_hash,
            request_bytes=prepared.request_bytes,
            provider_id=prepared.request.provider,
            model_id=prepared.request.model or "unknown",
            endpoint_class=prepared.request.endpoint_class,
            prompt_template_version=prepared.request.prompt_template_version,
            output_schema_version=role_context.output_schema_version,
            consent_epoch=team.consent_epoch,
            consent_hash=team.consent_hash,
            attempt_no=work.attempt_no,
            repair_of_model_call_id=repair_of,
            started_at=self.now(),
        )
        start_event = self._event(
            team=team,
            source="worker",
            event_type="MODEL_CALL_STARTED",
            key=f"call-start:{call.model_call_id}",
            payload={"model_call_id": call.model_call_id, "request_hash": call.request_hash},
        )
        updated_work, started_call = self.store.claim_agent_team_work_and_start_call(
            work_item_id=work.work_item_id,
            expected_fencing_token=work.fencing_token,
            owner=owner,
            now=self.now(),
            lease_expires_at=self.now() + timedelta(seconds=self.LEASE_SECONDS),
            call=call,
            event=start_event,
        )
        self._preflight_and_run(
            team=team,
            work=updated_work,
            snapshot=snapshot,
            role_context=role_context,
            consent=consent,
            prepared=prepared,
            call=started_call,
            owner=owner,
        )

    def _recover_started_call(
        self,
        *,
        team: AgentTeamRecord,
        work: AgentTeamWorkItem,
        snapshot,
        role_context,
        consent,
        wake: AgentTeamWakeRecord,
        owner: str,
    ) -> None:
        call = self.store.get_agent_team_model_call(team.project_id, work.model_call_id)
        if call is None or call.status != "started":
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        now = self.now()
        if work.lease_expires_at is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        if work.lease_expires_at > now:
            raise _RetryTeamWake(work.lease_expires_at)
        reclaim_event = self._event(
            team=team,
            source="scheduler",
            event_type="WORK_ITEM_CLAIMED",
            key=f"call-reclaim:{call.model_call_id}:{work.fencing_token + 1}",
            payload={"model_call_id": call.model_call_id, "fencing_token": work.fencing_token + 1},
        )
        updated_work, call = self.store.reclaim_agent_team_call(
            project_id=team.project_id,
            model_call_id=call.model_call_id,
            expected_fencing_token=work.fencing_token,
            owner=owner,
            now=now,
            lease_expires_at=now + timedelta(seconds=self.LEASE_SECONDS),
            event=reclaim_event,
        )
        if call.network_called:
            failed = AgentTeamModelCallRecord.model_validate({
                **call.model_dump(mode="python"),
                "status": "indeterminate",
                "error_code": "TEAM_SCHEDULER_FAILED",
                "completed_at": now,
            })
            self.store.fail_agent_team_model_call(
                project_id=team.project_id,
                model_call_id=call.model_call_id,
                owner=owner,
                fencing_token=updated_work.fencing_token,
                now=now,
                failed_call=failed,
                event=self._event(
                    team=team,
                    source="scheduler",
                    event_type="MODEL_CALL_INDETERMINATE",
                    key=f"call-indeterminate:{call.model_call_id}",
                    payload={"model_call_id": call.model_call_id, "reason": "scheduler_recovery"},
                ),
            )
            return

        prepared = self.worker_service.prepare_review(
            team=team,
            work_item=self._preparation_work(work, call),
            context_snapshot=snapshot,
            role_context=role_context,
            consent=consent,
            repair_of_model_call_id=call.repair_of_model_call_id,
            now=now,
        )
        if prepared.request_hash != call.request_hash or prepared.request_bytes != call.request_bytes:
            failed = AgentTeamModelCallRecord.model_validate({
                **call.model_dump(mode="python"),
                "status": "failed",
                "error_code": "AGENT_TEAM_CONTEXT_BINDING_INVALID",
                "completed_at": now,
            })
            self.store.fail_agent_team_model_call(
                project_id=team.project_id,
                model_call_id=call.model_call_id,
                owner=owner,
                fencing_token=updated_work.fencing_token,
                now=now,
                failed_call=failed,
                event=self._event(
                    team=team,
                    source="scheduler",
                    event_type="MODEL_CALL_FAILED",
                    key=f"call-failed:{call.model_call_id}",
                    payload={"model_call_id": call.model_call_id, "error_code": failed.error_code},
                ),
            )
            return
        self._preflight_and_run(
            team=team,
            work=updated_work,
            snapshot=snapshot,
            role_context=role_context,
            consent=consent,
            prepared=prepared,
            call=call,
            owner=owner,
        )

    def _preflight_and_run(
        self,
        *,
        team: AgentTeamRecord,
        work: AgentTeamWorkItem,
        snapshot,
        role_context,
        consent,
        prepared,
        call: AgentTeamModelCallRecord,
        owner: str,
    ) -> None:
        try:
            self.policy_service.preflight_provider_call(
                project_id=team.project_id,
                expected_consent_epoch=team.consent_epoch,
                expected_consent_hash=team.consent_hash,
                expected_global_policy_hash=team.global_policy_hash,
                reserved_input_tokens=prepared.estimated_input_tokens,
            )
        except SafetyError as exc:
            self._record_failed_call(
                team=team,
                work=work,
                call=call,
                owner=owner,
                code=getattr(exc, "code", None) or "MULTI_AGENT_CONSENT_INVALIDATED",
                metadata=None,
                allow_repair=False,
            )
            return
        marked_call = self.store.mark_agent_team_call_network_called(
            project_id=team.project_id,
            model_call_id=call.model_call_id,
            owner=owner,
            fencing_token=work.fencing_token,
            now=self.now(),
        )
        if marked_call.status != "started" or not marked_call.network_called:
            return
        try:
            result = self.worker_service.run_review(prepared)
        except AgentTeamWorkerError as exc:
            self._record_failed_call(
                team=team,
                work=work,
                call=marked_call,
                owner=owner,
                code=exc.code,
                metadata=exc.metadata,
                allow_repair=True,
            )
            return
        completed_at = self.now()
        completed_call = AgentTeamModelCallRecord.model_validate({
            **marked_call.model_dump(mode="python"),
            "status": "completed",
            "response_hash": result.metadata.response_hash,
            "schema_valid": True,
            "provider_request_id": result.metadata.provider_request_id,
            "input_tokens": result.metadata.input_tokens,
            "output_tokens": result.metadata.output_tokens,
            "cached_input_tokens": result.metadata.cached_input_tokens,
            "latency_ms": result.metadata.latency_ms,
            "completed_at": completed_at,
        })
        findings = tuple(
            self._bind_finding(
                team=team,
                work=work,
                call=completed_call,
                finding=finding,
                index=index,
                now=completed_at,
            )
            for index, finding in enumerate(result.findings)
        )
        self.store.complete_agent_team_model_call(
            project_id=team.project_id,
            model_call_id=completed_call.model_call_id,
            owner=owner,
            fencing_token=work.fencing_token,
            now=completed_at,
            completed_call=completed_call,
            findings=findings,
            event=self._event(
                team=team,
                source="worker",
                event_type="MODEL_CALL_COMPLETED",
                key=f"call-completed:{completed_call.model_call_id}",
                payload={
                    "model_call_id": completed_call.model_call_id,
                    "response_hash": completed_call.response_hash,
                },
                occurred_at=completed_at,
            ),
            wake=AgentTeamWakeRecord(
                wake_id=f"team_wake_{uuid4().hex}",
                team_id=team.team_id,
                team_epoch=team.team_epoch,
                project_id=team.project_id,
                lifecycle_id=team.lifecycle_id,
                step_key=f"aggregate:{completed_call.model_call_id}",
                reason="aggregate",
                available_at=completed_at,
                created_at=completed_at,
                updated_at=completed_at,
            ),
        )
        self.notify()

    def _record_failed_call(
        self,
        *,
        team: AgentTeamRecord,
        work: AgentTeamWorkItem,
        call: AgentTeamModelCallRecord,
        owner: str,
        code: str,
        metadata,
        allow_repair: bool,
    ) -> None:
        if code not in {
            "MULTI_AGENT_REVIEW_OUTPUT_INVALID",
            "MULTI_AGENT_REVIEWER_KIND_MISMATCH",
            "MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN",
            "MULTI_AGENT_REVIEW_INPUT_REF_INVALID",
            "MULTI_AGENT_REVIEW_FORBIDDEN_SUGGESTION",
            "MULTI_AGENT_DISABLED",
            "MULTI_AGENT_CONSENT_INVALIDATED",
            "MULTI_AGENT_BUDGET_EXCEEDED",
            "AGENT_MODEL_CONFIG_INCOMPLETE",
            "AGENT_MODEL_CONFIG_INVALID",
            "AGENT_MODEL_PROVIDER_FAILED",
            "AGENT_MODEL_PROVIDER_TIMEOUT",
            "AGENT_TEAM_PROVIDER_UNAVAILABLE",
            "AGENT_TEAM_CONTEXT_BINDING_INVALID",
            "AGENT_TEAM_PLANNING_STATE_INVALID",
            "TEAM_ROLE_REGISTRY_INVALID",
            "TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED",
            "TEAM_SCHEDULER_FAILED",
        }:
            code = "TEAM_SCHEDULER_FAILED"
        now = self.now()
        response_hash = metadata.response_hash if metadata is not None else None
        network_called = call.network_called
        indeterminate = network_called and (
            code in {"AGENT_MODEL_PROVIDER_TIMEOUT", "AGENT_MODEL_PROVIDER_FAILED"}
            or response_hash is None and code not in _REPAIRABLE_OUTPUT_ERRORS
        )
        retryable_output = (
            allow_repair
            and not indeterminate
            and code in _REPAIRABLE_OUTPUT_ERRORS
            and call.attempt_no == 1
            and team.budget.max_repairs > 0
            and metadata is not None
            and metadata.response_hash is not None
        )
        status = "indeterminate" if indeterminate else "failed"
        failed_call = AgentTeamModelCallRecord.model_validate({
            **call.model_dump(mode="python"),
            "status": status,
            "response_hash": response_hash,
            "schema_valid": False if retryable_output else None,
            "provider_request_id": metadata.provider_request_id if metadata else None,
            "input_tokens": metadata.input_tokens if metadata else None,
            "output_tokens": metadata.output_tokens if metadata else None,
            "cached_input_tokens": metadata.cached_input_tokens if metadata else None,
            "latency_ms": metadata.latency_ms if metadata else None,
            "error_code": code,
            "completed_at": now,
        })
        retry_wake = None
        if retryable_output:
            retry_wake = AgentTeamWakeRecord(
                wake_id=f"team_wake_{uuid4().hex}",
                team_id=team.team_id,
                team_epoch=team.team_epoch,
                project_id=team.project_id,
                lifecycle_id=team.lifecycle_id,
                step_key=f"work:{work.work_item_id}:attempt:2",
                reason="work_item",
                available_at=now,
                created_at=now,
                updated_at=now,
            )
        event_type = "MODEL_CALL_INDETERMINATE" if indeterminate else "MODEL_CALL_FAILED"
        self.store.fail_agent_team_model_call(
            project_id=team.project_id,
            model_call_id=call.model_call_id,
            owner=owner,
            fencing_token=work.fencing_token,
            now=now,
            failed_call=failed_call,
            event=self._event(
                team=team,
                source="worker",
                event_type=event_type,
                key=f"call-{status}:{call.model_call_id}",
                payload={
                    "model_call_id": call.model_call_id,
                    "error_code": code,
                    "response_hash": response_hash,
                },
                occurred_at=now,
            ),
            retry_wake=retry_wake,
        )
        self.notify()

    def _run_aggregation(self, wake: AgentTeamWakeRecord) -> None:
        team = self.store.get_agent_team(wake.project_id, wake.team_id)
        if team is None or team.status == "COMPLETED":
            return
        work_items = self.store.list_agent_team_work_items(wake.project_id, wake.team_id)
        if len(work_items) != 3 or any(item.status != "COMPLETED" for item in work_items):
            return
        self.coordinator_service.finalize_team(
            project_id=wake.project_id,
            team_id=wake.team_id,
            now=self.now(),
        )
        self.notify()

    @staticmethod
    def _preparation_work(
        work: AgentTeamWorkItem, call: AgentTeamModelCallRecord
    ) -> AgentTeamWorkItem:
        return AgentTeamWorkItem.model_validate({
            **work.model_dump(mode="python"),
            "status": "RETRY_READY" if call.attempt_no == 2 else "PENDING",
            "model_call_id": call.repair_of_model_call_id,
            "request_hash": None,
            "lease_owner": None,
            "lease_expires_at": None,
        })

    @staticmethod
    def _bind_finding(
        *, team: AgentTeamRecord, work: AgentTeamWorkItem, call: AgentTeamModelCallRecord,
        finding, index: int, now: datetime,
    ) -> AgentTeamFinding:
        payload = {
            **finding.model_dump(mode="python"),
            "finding_id": f"team_finding_{call.model_call_id}_{index}",
            "team_id": team.team_id,
            "team_epoch": team.team_epoch,
            "project_id": team.project_id,
            "lifecycle_id": team.lifecycle_id,
            "work_item_id": work.work_item_id,
            "role_id": work.role_id,
            "role_context_hash": call.role_context_hash,
            "model_call_id": call.model_call_id,
            "model_response_hash": call.response_hash,
            "created_at": now,
        }
        return AgentTeamFinding(
            finding_hash=agent_team_finding_hash(payload),
            **payload,
        )

    @staticmethod
    def _work_item_id(step_key: str) -> str:
        if not step_key.startswith("work:") or ":attempt:" not in step_key:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        work_item_id, attempt = step_key[5:].rsplit(":attempt:", 1)
        if not work_item_id or attempt not in {"1", "2"}:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        return work_item_id

    @staticmethod
    def _work_attempt_no(step_key: str) -> int:
        if ":attempt:" not in step_key:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        attempt = step_key.rsplit(":attempt:", 1)[1]
        if attempt not in {"1", "2"}:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        return int(attempt)

    def _event(
        self,
        *, team: AgentTeamRecord, source: str, event_type: str, key: str,
        payload: dict[str, object], occurred_at: datetime | None = None,
    ) -> AgentTeamEvent:
        event_time = occurred_at or self.now()
        return AgentTeamEvent(
            event_id=f"team_event_{uuid4().hex}",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            command_id=key,
            idempotency_key=key,
            source=source,
            event_type=event_type,
            payload_hash=canonical_hash(payload),
            occurred_at=event_time,
        )

    @staticmethod
    def _lost_wake_lease(exc: Exception) -> bool:
        return isinstance(exc, RuntimeError) and str(exc) in {
            "AGENT_TEAM_WAKE_CONCURRENT_UPDATE",
            "AGENT_TEAM_WAKE_OWNER_MISMATCH",
        }

    def _start_workers(self) -> None:
        with self._condition:
            live = [worker for worker in self._workers if worker.is_alive()]
            self._workers = live
            while self._accepting and len(self._workers) < self.MAX_CONCURRENCY:
                worker = Thread(
                    target=self._run_worker,
                    name=f"agent-team-scheduler-{len(self._workers) + 1}",
                    daemon=True,
                )
                self._workers.append(worker)
                worker.start()

    def _run_worker(self) -> None:
        owner = f"agent-team-scheduler:{uuid4().hex}"
        while self._accepting:
            if self.run_once(owner=owner) is not None:
                continue
            next_due = self.store.next_agent_team_wake_at()
            with self._condition:
                if not self._accepting:
                    break
                timeout = (
                    max(0.0, (next_due - self.now()).total_seconds())
                    if next_due is not None
                    else None
                )
                self._condition.wait(timeout=timeout)
        with self._condition:
            self._workers = [worker for worker in self._workers if worker.is_alive()]
