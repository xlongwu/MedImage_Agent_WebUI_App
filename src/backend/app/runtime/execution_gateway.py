"""The sole production dispatch boundary for reviewed pipeline execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import executable_contract_versions
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.gateway_dispatch import GatewayDispatch, GatewayDispatchEvent
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.execution_environment_service import ExecutionEnvironmentService

_VERIFICATION_SENTINEL = object()


def _default_pipeline_executor(**kwargs: Any) -> dict[str, Any]:
    from src.backend.app.runtime.pipeline_executor import run_pipeline

    return run_pipeline(**kwargs)


PIPELINE_EXECUTOR: Callable[..., dict[str, Any]] = _default_pipeline_executor


def current_allowlist_hash() -> str:
    """Fingerprint the executable registry so issued authority expires on drift."""
    return stable_hash({
        "policy_version": 2,
        "contracts": executable_contract_versions(),
    })


@dataclass(frozen=True)
class VerifiedExecutionContext:
    ticket: ExecutionTicket
    ticket_service: ExecutionTicketService
    verified_project_config_path: str
    verified_pipeline_path: str
    verification_id: str
    dispatch: GatewayDispatch
    _sentinel: object = field(repr=False, compare=False)


def assert_verified_execution_context(context: VerifiedExecutionContext | None) -> None:
    if not isinstance(context, VerifiedExecutionContext) or context._sentinel is not _VERIFICATION_SENTINEL:
        raise SafetyError(
            "VERIFIED_EXECUTION_CONTEXT_REQUIRED",
            code="VERIFIED_EXECUTION_CONTEXT_REQUIRED",
        )


class ExecutionGateway:
    def __init__(
        self,
        ticket_service: ExecutionTicketService,
        *,
        environment_service: ExecutionEnvironmentService | None = None,
    ) -> None:
        self.ticket_service = ticket_service
        self.environment_service = environment_service or ExecutionEnvironmentService(
            ticket_service.store
        )

    def dispatch(
        self,
        *,
        execution_ticket_id: str,
        project_id: str,
        reviewed_plan_id: str,
        plan_hash: str,
        approval_summary_hash: str,
        memory_context_hash: str | None,
        scope_hash: str,
        normalized_params_hash: str,
        contract_versions: dict[str, str] | tuple[tuple[str, str], ...],
        project_config_path: str,
        pipeline_path: str,
        command_id: str,
        run_id: str,
        goal_contract_hash: str | None = None,
        evaluation_policy_version: str | None = None,
        executor: Callable[..., dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], ExecutionTicket]:
        fingerprint = current_allowlist_hash()
        existing = self.ticket_service.store.get_gateway_dispatch_by_command(command_id)
        replay_key = existing.dispatch_id if existing is not None else None
        ticket = self.ticket_service.validate(
            execution_ticket_id,
            project_id=project_id,
            reviewed_plan_id=reviewed_plan_id,
            plan_hash=plan_hash,
            approval_summary_hash=approval_summary_hash,
            memory_context_hash=memory_context_hash,
            scope_hash=scope_hash,
            allowlist_hash=fingerprint,
            normalized_params_hash=normalized_params_hash,
            contract_versions=contract_versions,
            project_config_path=project_config_path,
            pipeline_path=pipeline_path,
            goal_contract_hash=goal_contract_hash,
            evaluation_policy_version=evaluation_policy_version,
            replay_idempotency_key=replay_key,
        )
        # This must remain ahead of dispatch-record creation and ticket
        # consumption. A changed host can never turn an old approval into a run.
        try:
            self.environment_service.verify_for_dispatch(execution_ticket=ticket)
        except SafetyError as exc:
            reason = str(exc.code or "EXECUTION_ENVIRONMENT_CHANGED")
            self.ticket_service.record_rejection(
                project_id=ticket.project_id,
                ticket_id=ticket.execution_ticket_id,
                audit_id=ticket.audit_id,
                reason=reason,
            )
            raise
        identity = {
            "schema_version": 1,
            "command_id": command_id,
            "project_id": project_id,
            "reviewed_plan_id": reviewed_plan_id,
            "execution_ticket_id": ticket.execution_ticket_id,
            "approval_summary_hash": approval_summary_hash,
            "execution_environment_snapshot_id": ticket.execution_environment_snapshot_id,
            "execution_environment_hash": ticket.execution_environment_hash,
            "plan_hash": plan_hash,
            "memory_context_hash": memory_context_hash,
            "scope_hash": scope_hash,
            "allowlist_hash": fingerprint,
            "run_id": run_id,
        }
        if existing is not None and existing.canonical_hash != stable_hash(identity):
            raise SafetyError(
                "GATEWAY_DISPATCH_COMMAND_CONFLICT",
                code="GATEWAY_DISPATCH_COMMAND_CONFLICT",
            )
        candidate = GatewayDispatch(
            dispatch_id=existing.dispatch_id if existing else f"dispatch_{uuid4().hex}",
            created_at=existing.created_at if existing else datetime.now(UTC),
            canonical_hash=stable_hash(identity),
            **{key: value for key, value in identity.items() if key != "schema_version"},
        )
        try:
            dispatch = self.ticket_service.store.add_gateway_dispatch(candidate)
        except Exception as exc:
            raise StateStoreError("GATEWAY_DISPATCH_WRITE_FAILED") from exc

        events = self.ticket_service.store.list_gateway_dispatch_events(dispatch.dispatch_id)
        succeeded = next(
            (event for event in events if event.event_type == "dispatch_succeeded"),
            None,
        )
        if succeeded is not None and succeeded.result is not None:
            return dict(succeeded.result), ticket
        failed = next(
            (
                event
                for event in events
                if event.event_type in {"dispatch_failed", "dispatch_rejected"}
            ),
            None,
        )
        if failed is not None:
            code = failed.failure_code or "EXECUTION_DISPATCH_FAILED"
            raise SafetyError(code, code=code)
        if any(event.event_type == "dispatch_started" for event in events):
            raise SafetyError(
                "GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
                code="GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
            )

        consumed = self.ticket_service.consume(
            ticket,
            idempotency_key=dispatch.dispatch_id,
        )
        started = GatewayDispatchEvent(
            event_id=f"dispatch_event_{uuid4().hex}",
            dispatch_id=dispatch.dispatch_id,
            project_id=dispatch.project_id,
            event_type="dispatch_started",
            occurred_at=datetime.now(UTC),
            redacted_summary="Gateway accepted the bound ticket and started one executor call.",
        )
        try:
            self.ticket_service.store.add_gateway_dispatch_event(started)
        except Exception as exc:
            current_events = self.ticket_service.store.list_gateway_dispatch_events(
                dispatch.dispatch_id
            )
            if any(event.event_type == "dispatch_started" for event in current_events):
                raise SafetyError(
                    "GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
                    code="GATEWAY_DISPATCH_OUTCOME_UNKNOWN",
                ) from exc
            raise StateStoreError("GATEWAY_DISPATCH_EVENT_WRITE_FAILED") from exc
        context = VerifiedExecutionContext(
            ticket=consumed,
            ticket_service=self.ticket_service,
            verified_project_config_path=str(Path(project_config_path).resolve()),
            verified_pipeline_path=str(Path(pipeline_path).resolve()),
            verification_id=f"verification_{uuid4().hex}",
            dispatch=dispatch,
            _sentinel=_VERIFICATION_SENTINEL,
        )
        if executor is None:
            executor = PIPELINE_EXECUTOR
        try:
            result = executor(
                project_config_path=project_config_path,
                pipeline_path=pipeline_path,
                execution_context=context,
            )
            result = dict(result)
            actual_run_id = str(result.get("run_id") or "")
            if actual_run_id and actual_run_id != dispatch.run_id:
                raise SafetyError(
                    "EXECUTOR_RUN_ID_MISMATCH",
                    code="EXECUTOR_RUN_ID_MISMATCH",
                )
            result.setdefault("run_id", dispatch.run_id)
            result["dispatch_id"] = dispatch.dispatch_id
            event = GatewayDispatchEvent(
                event_id=f"dispatch_event_{uuid4().hex}",
                dispatch_id=dispatch.dispatch_id,
                project_id=dispatch.project_id,
                event_type="dispatch_succeeded",
                occurred_at=datetime.now(UTC),
                result_hash=stable_hash(result),
                redacted_summary="Executor returned a persisted gateway result.",
                result=result,
            )
            self.ticket_service.store.add_gateway_dispatch_event(event)
            return result, consumed
        except Exception as exc:
            code = (
                str(exc.code)
                if isinstance(exc, SafetyError) and exc.code
                else "EXECUTION_DISPATCH_FAILED"
            )
            event = GatewayDispatchEvent(
                event_id=f"dispatch_event_{uuid4().hex}",
                dispatch_id=dispatch.dispatch_id,
                project_id=dispatch.project_id,
                event_type="dispatch_failed",
                occurred_at=datetime.now(UTC),
                failure_code=code,
                redacted_summary=f"Executor stopped with {type(exc).__name__}.",
            )
            try:
                self.ticket_service.store.add_gateway_dispatch_event(event)
            except Exception as event_exc:
                raise StateStoreError("GATEWAY_DISPATCH_OUTCOME_WRITE_FAILED") from event_exc
            raise
