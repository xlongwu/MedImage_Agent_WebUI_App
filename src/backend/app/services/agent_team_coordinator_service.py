"""Create a consent-bound Team through the existing lifecycle/store transaction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.backend.app.core.config_schema import AgentModelRuntimeConfig, AgentMultiAgentConfig
from src.backend.app.schemas.agent_harness import AgentPlanningBaseContext
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_team import (
    AgentTeamAdvisory,
    AgentTeamBudget,
    AgentTeamBudgetSlice,
    AgentTeamEvent,
    AgentTeamRecord,
    AgentTeamWakeRecord,
    AgentTeamWorkItem,
    agent_team_advisory_hash,
    canonical_hash,
)
from src.backend.app.services.agent_multi_agent_policy_service import (
    AgentMultiAgentPolicyService,
    multi_agent_policy_hash,
)
from src.backend.app.services.agent_review_context_projector import context_projector_hash
from src.backend.app.services.agent_review_finding_aggregator import (
    AgentReviewFindingAggregator,
    aggregation_policy_hash,
)
from src.backend.app.services.agent_review_role_registry import role_for, role_registry_hash
from src.backend.app.services.agent_team_context_service import AgentTeamContextService
from src.backend.app.services.agent_team_eligibility_policy import (
    AgentTeamEligibilityDecision,
    AgentTeamEligibilityInput,
    AgentTeamEligibilityPolicy,
)
from src.backend.app.services.structured_agent_model_adapter import team_review_model_profile_hash


class AgentTeamIneligibleError(RuntimeError):
    def __init__(self, decision: AgentTeamEligibilityDecision) -> None:
        super().__init__(decision.reason_code)
        self.decision = decision
        self.code = decision.reason_code


class AgentTeamCoordinatorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AgentTeamCreationResult:
    team: AgentTeamRecord
    eligibility: AgentTeamEligibilityDecision


class AgentTeamCoordinatorService:
    """Own deterministic Team creation; it never invokes a reviewer model."""

    _PLANNING_STATES = frozenset({"CREATED", "CONTEXT_READY", "PLAN_DRAFTED"})
    _ROLE_KINDS = ("science", "safety", "completeness")

    def __init__(
        self,
        *,
        store,
        policy_service: AgentMultiAgentPolicyService,
        multi_agent_config: AgentMultiAgentConfig,
        model_config: AgentModelRuntimeConfig,
        context_service: AgentTeamContextService | None = None,
        eligibility_policy: AgentTeamEligibilityPolicy | None = None,
    ) -> None:
        self.store = store
        self.policy_service = policy_service
        self.multi_agent_config = multi_agent_config
        self.model_config = model_config
        self.context_service = context_service or AgentTeamContextService()
        self.eligibility_policy = eligibility_policy or AgentTeamEligibilityPolicy()
        self.finding_aggregator = AgentReviewFindingAggregator()

    def create_team(
        self,
        *,
        lifecycle: AgentLifecycleRecord,
        base_context: AgentPlanningBaseContext,
        eligibility_input: AgentTeamEligibilityInput,
        command_id: str,
        actor: str,
        now: datetime | None = None,
    ) -> AgentTeamCreationResult:
        current_time = now or datetime.now(UTC)
        decision = self.eligibility_policy.evaluate(eligibility_input)
        requested_model_profile_hash = team_review_model_profile_hash(
            config=self.model_config,
            max_output_tokens=self.multi_agent_config.max_output_tokens_per_call,
        )
        request_payload_hash = canonical_hash({
            "lifecycle_id": lifecycle.lifecycle_id,
            "project_id": lifecycle.project_id,
            "base_context_hash": base_context.base_context_hash,
            "eligibility_input_hash": decision.input_hash,
            "consent_epoch": eligibility_input.project_consent_epoch,
            "consent_hash": eligibility_input.project_consent_hash,
            "model_profile_hash": requested_model_profile_hash,
            "role_registry_hash": role_registry_hash(),
            "context_projector_hash": context_projector_hash(),
            "aggregation_policy_hash": aggregation_policy_hash(),
            "max_model_calls": min(self.multi_agent_config.max_model_calls, 6),
            "max_input_tokens": self.multi_agent_config.max_input_tokens,
            "max_output_tokens": self.multi_agent_config.max_output_tokens_per_call,
        })
        prior_creation = self.store.get_agent_team_creation_by_command(
            lifecycle.project_id, lifecycle.lifecycle_id, command_id
        )
        if prior_creation is not None:
            prior_team, prior_event = prior_creation
            if prior_event.payload_hash != request_payload_hash:
                raise AgentTeamCoordinatorError("AGENT_TEAM_COMMAND_REPLAYED")
            if not decision.eligible:
                raise AgentTeamIneligibleError(decision)
            return AgentTeamCreationResult(team=prior_team, eligibility=decision)
        max_model_calls = min(
            self.multi_agent_config.max_model_calls,
            6,
        )
        max_input_tokens = self.multi_agent_config.max_input_tokens
        consent = self.policy_service.verify_team_budget(
            project_id=lifecycle.project_id,
            max_model_calls=max_model_calls,
            reserved_input_tokens=max_input_tokens,
        )
        if (
            eligibility_input.project_id != lifecycle.project_id
            or eligibility_input.project_consent_enabled != consent.enabled
            or eligibility_input.project_consent_epoch != consent.consent_epoch
            or eligibility_input.project_consent_hash != consent.consent_hash
        ):
            raise AgentTeamCoordinatorError("MULTI_AGENT_CONSENT_INVALIDATED")
        if not decision.eligible:
            raise AgentTeamIneligibleError(decision)
        if self.model_config.provider != "openai_compatible":
            raise AgentTeamCoordinatorError("AGENT_TEAM_PROVIDER_UNAVAILABLE")
        if self.model_config.incomplete_reason() is not None:
            raise AgentTeamCoordinatorError("AGENT_MODEL_CONFIG_INCOMPLETE")
        if lifecycle.state not in self._PLANNING_STATES:
            raise AgentTeamCoordinatorError("AGENT_TEAM_PLANNING_STATE_INVALID")
        if (
            base_context.project_id != lifecycle.project_id
            or base_context.lifecycle_id != lifecycle.lifecycle_id
            or not base_context.complete
        ):
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

        expected_epoch = lifecycle.planning_team_epoch
        team_epoch = expected_epoch + 1
        if base_context.planning_epoch != team_epoch:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

        effective_max_calls = min(max_model_calls, consent.max_model_calls)
        total_input_tokens = min(max_input_tokens, consent.max_input_tokens)
        if effective_max_calls < 3 or total_input_tokens < 3:
            raise AgentTeamCoordinatorError("MULTI_AGENT_BUDGET_EXCEEDED")
        max_repairs = min(3, effective_max_calls - 3)
        team_deadline = current_time + timedelta(
            seconds=min(self.multi_agent_config.max_team_deadline_seconds, 300)
        )
        role_deadline = min(
            current_time + timedelta(
                seconds=self.multi_agent_config.max_role_deadline_seconds
            ),
            team_deadline,
        )
        role_input_tokens = total_input_tokens // 3
        budget = AgentTeamBudget(
            max_model_calls=effective_max_calls,
            max_repairs=max_repairs,
            reserved_input_tokens=role_input_tokens * 3,
            deadline_at=team_deadline,
        )
        slices = {
            kind: AgentTeamBudgetSlice(
                max_model_calls=2,
                reserved_input_tokens=role_input_tokens,
                deadline_at=role_deadline,
            )
            for kind in self._ROLE_KINDS
        }
        model_profile_hash = requested_model_profile_hash
        team_id = f"team_{uuid4().hex}"
        context_snapshot, role_contexts = self.context_service.project_snapshot(
            base_context=base_context,
            team_id=team_id,
            team_epoch=team_epoch,
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            model_profile_hash=model_profile_hash,
            consent=consent,
            budget_slices=slices,
        )
        team = AgentTeamRecord(
            team_id=team_id,
            team_epoch=team_epoch,
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            snapshot_hash=context_snapshot.snapshot_hash,
            role_registry_hash=role_registry_hash(),
            context_projector_hash=context_projector_hash(),
            aggregation_policy_hash=aggregation_policy_hash(),
            model_profile_hash=model_profile_hash,
            global_policy_hash=multi_agent_policy_hash(self.multi_agent_config),
            consent_epoch=consent.consent_epoch,
            consent_hash=consent.consent_hash,
            budget=budget,
            created_at=current_time,
            updated_at=current_time,
        )
        work_items = tuple(
            AgentTeamWorkItem(
                work_item_id=f"{team_id}:{kind}",
                team_id=team_id,
                team_epoch=team_epoch,
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                reviewer_kind=kind,
                role_id=role_for(kind).role_id,
                dependency_ids=(),
                available_at=current_time,
                deadline_at=role_deadline,
                created_at=current_time,
                updated_at=current_time,
            )
            for kind in self._ROLE_KINDS
        )
        lifecycle_record = lifecycle.model_copy(
            update={
                "planning_mode": "multi_agent",
                "planning_team_id": team_id,
                "planning_team_epoch": team_epoch,
                "last_command_id": command_id,
                "updated_at": current_time,
            }
        )
        details = {
            "planning_mode": "multi_agent",
            "planning_team_id": team_id,
            "planning_team_epoch": team_epoch,
            "base_context_hash": base_context.base_context_hash,
            "eligibility_input_hash": decision.input_hash,
        }
        lifecycle_event = AgentLifecycleEvent(
            event_id=f"lifecycle_event_{uuid4().hex}",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            command_id=command_id,
            actor=actor,
            source_command="create_multi_agent_team",
            occurred_at=current_time,
            from_state=lifecycle.state,
            to_state=lifecycle.state,
            reason="MULTI_AGENT_ELIGIBLE",
            details=details,
        )
        event = AgentTeamEvent(
            event_id=f"team_event_{uuid4().hex}",
            team_id=team_id,
            team_epoch=team_epoch,
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            command_id=command_id,
            idempotency_key=f"create:{command_id}",
            source="coordinator",
            event_type="TEAM_REQUESTED",
            payload_hash=request_payload_hash,
            occurred_at=current_time,
        )
        wakes = tuple(
            AgentTeamWakeRecord(
                wake_id=f"team_wake_{uuid4().hex}",
                team_id=team_id,
                team_epoch=team_epoch,
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                step_key=f"work:{item.work_item_id}:attempt:{item.attempt_no}",
                reason="work_item",
                available_at=current_time,
                created_at=current_time,
                updated_at=current_time,
            )
            for item in work_items
        )
        persisted_team = self.store.create_agent_team_bundle(
            lifecycle_record=lifecycle_record,
            lifecycle_event=lifecycle_event,
            expected_team_id=lifecycle.planning_team_id,
            expected_team_epoch=expected_epoch,
            base_context=base_context,
            team=team,
            context_snapshot=context_snapshot,
            role_contexts=role_contexts,
            work_items=work_items,
            event=event,
            wakes=wakes,
        )
        return AgentTeamCreationResult(team=persisted_team, eligibility=decision)

    def finalize_team(
        self, *, project_id: str, team_id: str, now: datetime | None = None
    ) -> AgentTeamAdvisory:
        current_time = now or datetime.now(UTC)
        team = self.store.get_agent_team(project_id, team_id)
        if team is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_NOT_FOUND")
        if team.status == "COMPLETED":
            prior = self.store.get_agent_team_advisory(project_id, team_id)
            if prior is None:
                raise AgentTeamCoordinatorError("AGENT_TEAM_ADVISORY_MISSING")
            return prior
        if team.status not in {"RUNNING", "AGGREGATING"}:
            raise AgentTeamCoordinatorError("AGENT_TEAM_PLANNING_STATE_INVALID")
        consent = self.policy_service.verify_team_budget(
            project_id=project_id,
            max_model_calls=team.budget.max_model_calls,
            reserved_input_tokens=team.budget.reserved_input_tokens,
        )
        if (
            consent.consent_epoch != team.consent_epoch
            or consent.consent_hash != team.consent_hash
            or team.role_registry_hash != role_registry_hash()
            or team.context_projector_hash != context_projector_hash()
            or team.aggregation_policy_hash != aggregation_policy_hash()
            or team.global_policy_hash
            != multi_agent_policy_hash(self.multi_agent_config)
            or team.model_profile_hash
            != team_review_model_profile_hash(
                config=self.model_config,
                max_output_tokens=self.multi_agent_config.max_output_tokens_per_call,
            )
        ):
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        context_pair = self.store.get_agent_team_context(project_id, team_id)
        if context_pair is None:
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        snapshot, role_contexts = context_pair
        base = self.store.get_agent_planning_base_context(
            project_id, snapshot.base_context_hash
        )
        if (
            base is None
            or snapshot.snapshot_hash != team.snapshot_hash
            or snapshot.role_registry_hash != team.role_registry_hash
            or snapshot.context_projector_hash != team.context_projector_hash
            or snapshot.consent_epoch != consent.consent_epoch
            or snapshot.consent_hash != consent.consent_hash
            or set(role_contexts) != set(self._ROLE_KINDS)
        ):
            raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

        work_items = self.store.list_agent_team_work_items(project_id, team_id)
        if len(work_items) != 3 or {item.status for item in work_items} != {"COMPLETED"}:
            raise AgentTeamCoordinatorError("AGENT_TEAM_PLANNING_STATE_INVALID")
        calls = self.store.list_agent_team_model_calls(project_id, team_id)
        calls_by_id = {call.model_call_id: call for call in calls}
        for item in work_items:
            call = calls_by_id.get(item.model_call_id or "")
            if (
                call is None
                or call.status != "completed"
                or call.work_item_id != item.work_item_id
                or call.attempt_no != item.attempt_no
                or call.response_hash is None
            ):
                raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

        role_registry = {kind: role_for(kind) for kind in self._ROLE_KINDS}
        all_findings = tuple(self.store.list_agent_team_findings(project_id, team_id))
        allowed_refs: set[str] = set()
        allowed_codes: set[str] = set()
        for kind, role_context in role_contexts.items():
            role = role_registry[kind]
            if (
                role_context.role_context_hash
                != snapshot.role_context_hashes[kind]
                or role_context.base_context_hash != base.base_context_hash
                or role_context.consent_epoch != team.consent_epoch
                or role_context.consent_hash != team.consent_hash
            ):
                raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
            allowed_refs.update(role_context.evidence_refs)
            allowed_codes.update(role.allowed_finding_codes)
        for finding in all_findings:
            call = calls_by_id.get(finding.model_call_id)
            role_context = role_contexts.get(finding.reviewer_kind)
            role = role_registry[finding.reviewer_kind]
            if (
                call is None
                or role_context is None
                or call.status != "completed"
                or finding.team_id != team.team_id
                or finding.team_epoch != team.team_epoch
                or finding.project_id != project_id
                or finding.lifecycle_id != team.lifecycle_id
                or finding.role_id != role.role_id
                or finding.role_context_hash != role_context.role_context_hash
                or finding.work_item_id != call.work_item_id
                or finding.model_response_hash != call.response_hash
                or finding.code not in role.allowed_finding_codes
                or not set(finding.input_refs).issubset(role_context.evidence_refs)
            ):
                raise AgentTeamCoordinatorError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

        aggregation = self.finding_aggregator.aggregate(
            findings=all_findings,
            input_refs=tuple(sorted(allowed_refs)),
            allowed_codes=tuple(sorted(allowed_codes)),
            required_safety=True,
            completed_reviewer_kinds=tuple(self._ROLE_KINDS),
        )
        if aggregation.rejected_codes:
            raise AgentTeamCoordinatorError(aggregation.rejected_codes[0])
        accepted_ids = tuple(sorted(
            min(
                finding.finding_id
                for finding in all_findings
                if finding.reviewer_kind == accepted.reviewer_kind
                and finding.code == accepted.code
                and finding.severity == accepted.severity
                and tuple(sorted(finding.input_refs)) == tuple(sorted(accepted.input_refs))
            )
            for accepted in aggregation.accepted
        ))
        rejected_ids = tuple(sorted(
            finding.finding_id
            for finding in all_findings
            if finding.finding_id not in accepted_ids
        ))
        blocking_codes = tuple(sorted({
            item.code for item in aggregation.accepted if item.severity == "blocking"
        }))
        decision = (
            "accepted"
            if aggregation.safety_reviewer_present
            and not aggregation.conflict_codes
            and not blocking_codes
            else "rejected"
        )
        advisory_payload = {
            "schema_version": 1,
            "advisory_id": f"team_advisory_{team.team_id}_{team.team_epoch}",
            "team_id": team.team_id,
            "team_epoch": team.team_epoch,
            "project_id": project_id,
            "lifecycle_id": team.lifecycle_id,
            "base_context_hash": base.base_context_hash,
            "context_snapshot_hash": snapshot.snapshot_hash,
            "context_projector_hash": team.context_projector_hash,
            "role_registry_hash": team.role_registry_hash,
            "aggregation_policy_hash": team.aggregation_policy_hash,
            "model_profile_hash": team.model_profile_hash,
            "consent_epoch": team.consent_epoch,
            "consent_hash": team.consent_hash,
            "policy_version": snapshot.policy_version,
            "redaction_policy_version": snapshot.redaction_policy_version,
            "accepted_finding_ids": accepted_ids,
            "rejected_finding_ids": rejected_ids,
            "blocking_codes": blocking_codes,
            "conflict_codes": aggregation.conflict_codes,
            "coverage_by_role": {
                kind: sum(finding.reviewer_kind == kind for finding in all_findings)
                for kind in self._ROLE_KINDS
            },
            "safety_reviewer_present": aggregation.safety_reviewer_present,
            "decision": decision,
            "created_at": current_time,
        }
        advisory = AgentTeamAdvisory(
            advisory_hash=agent_team_advisory_hash(advisory_payload),
            **advisory_payload,
        )
        event = AgentTeamEvent(
            event_id=f"team_event_{uuid4().hex}",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=project_id,
            lifecycle_id=team.lifecycle_id,
            command_id=f"finalize:{team.team_id}:{team.team_epoch}",
            idempotency_key=f"advisory:{team.team_epoch}",
            source="coordinator",
            event_type="TEAM_ADVISORY_FROZEN",
            payload_hash=advisory.advisory_hash,
            occurred_at=current_time,
        )
        wake = AgentTeamWakeRecord(
            wake_id=f"team_wake_{uuid4().hex}",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=project_id,
            lifecycle_id=team.lifecycle_id,
            step_key=f"planning_resume:{team.team_id}:{team.team_epoch}",
            reason="planning_resume",
            available_at=current_time,
            created_at=current_time,
            updated_at=current_time,
        )
        return self.store.complete_agent_team_aggregation(
            advisory=advisory,
            event=event,
            wake=wake,
            now=current_time,
        )
