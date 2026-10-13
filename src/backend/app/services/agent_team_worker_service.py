"""Pure, fixed-role Team review worker with no store or execution dependency."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from src.backend.app.core.config_schema import (
    AgentModelRuntimeConfig,
    AgentMultiAgentConfig,
    multi_agent_policy_hash,
)
from src.backend.app.planner.agent_model_adapter import (
    MAX_COMPLETE_REQUEST_BYTES,
    assert_complete_request_within_limit,
    canonical_request_bytes,
)
from src.backend.app.schemas.agent_eval import AgentReviewFinding
from src.backend.app.schemas.agent_harness import AgentTeamRoleContext, CanonicalModelRequest
from src.backend.app.schemas.agent_team import (
    AgentTeamContextSnapshot,
    AgentTeamRecord,
    AgentTeamWorkItem,
)
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.agent_review_context_projector import context_projector_hash
from src.backend.app.services.agent_review_finding_aggregator import AgentReviewFindingAggregator
from src.backend.app.services.agent_review_role_registry import role_for, role_registry_hash
from src.backend.app.services.structured_agent_model_adapter import (
    OpenAICompatibleStructuredProvider,
    StructuredAgentModelAdapter,
    StructuredModelCallError,
    StructuredReviewMetadata,
    team_review_model_profile_hash,
    team_review_output_schema,
)

TEAM_REVIEW_REQUEST_BUILDER_VERSION = "agent-team-review-request-v1"
_WORKER_ERRORS = {
    "AGENT_TEAM_PROVIDER_UNAVAILABLE",
    "AGENT_MODEL_CONFIG_INCOMPLETE",
    "AGENT_MODEL_CONFIG_INVALID",
    "AGENT_MODEL_PROVIDER_FAILED",
    "AGENT_MODEL_PROVIDER_TIMEOUT",
    "AGENT_TEAM_PROVIDER_UNAVAILABLE",
    "AGENT_TEAM_CONTEXT_BINDING_INVALID",
    "MULTI_AGENT_DISABLED",
    "MULTI_AGENT_CONSENT_INVALIDATED",
    "MULTI_AGENT_BUDGET_EXCEEDED",
    "MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN",
    "MULTI_AGENT_REVIEW_INPUT_REF_INVALID",
    "MULTI_AGENT_REVIEW_FORBIDDEN_SUGGESTION",
    "MULTI_AGENT_REVIEW_OUTPUT_INVALID",
    "MULTI_AGENT_REVIEWER_KIND_MISMATCH",
    "TEAM_ROLE_REGISTRY_INVALID",
    "TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED",
}


@dataclass(frozen=True)
class PreparedAgentTeamReview:
    team: AgentTeamRecord
    work_item: AgentTeamWorkItem
    context_snapshot_hash: str
    role_context: AgentTeamRoleContext
    request: CanonicalModelRequest
    request_hash: str
    request_bytes: int
    estimated_input_tokens: int
    repair_of_model_call_id: str | None


@dataclass(frozen=True)
class AgentTeamReviewResult:
    findings: tuple[AgentReviewFinding, ...]
    request_hash: str
    request_bytes: int
    metadata: StructuredReviewMetadata


class AgentTeamWorkerError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        prepared: PreparedAgentTeamReview | None = None,
        metadata: StructuredReviewMetadata | None = None,
    ) -> None:
        safe_code = code if code in _WORKER_ERRORS else "MULTI_AGENT_REVIEW_OUTPUT_INVALID"
        super().__init__(safe_code)
        self.code = safe_code
        self.prepared = prepared
        self.metadata = metadata


class AgentTeamWorkerService:
    """Prepare and validate one role request; persistence stays with its caller."""

    def __init__(
        self,
        *,
        multi_agent_config: AgentMultiAgentConfig,
        model_config: AgentModelRuntimeConfig,
        adapter: StructuredAgentModelAdapter | None = None,
    ) -> None:
        self.multi_agent_config = multi_agent_config
        self.model_config = model_config.model_copy(
            update={
                "max_output_tokens": min(
                    model_config.max_output_tokens,
                    multi_agent_config.max_output_tokens_per_call,
                )
            }
        )
        self.adapter = adapter or StructuredAgentModelAdapter(
            OpenAICompatibleStructuredProvider(self.model_config)
        )
        self.finding_aggregator = AgentReviewFindingAggregator()

    def prepare_review(
        self,
        *,
        team: AgentTeamRecord,
        work_item: AgentTeamWorkItem,
        context_snapshot: AgentTeamContextSnapshot,
        role_context: AgentTeamRoleContext,
        consent: ProjectMultiAgentConsent,
        repair_of_model_call_id: str | None = None,
        now: datetime | None = None,
    ) -> PreparedAgentTeamReview:
        current_time = now or datetime.now(UTC)
        self._validate_binding(
            team=team,
            work_item=work_item,
            context_snapshot=context_snapshot,
            role_context=role_context,
            consent=consent,
            repair_of_model_call_id=repair_of_model_call_id,
            now=current_time,
        )
        role = role_for(work_item.reviewer_kind)
        output_schema = self._role_output_schema(role_context, role.allowed_finding_codes)
        request = CanonicalModelRequest(
            provider=self.model_config.provider,
            model=self.model_config.model,
            endpoint_class="chat_completions",
            prompt_template_version=role.prompt_template_version,
            system_prompt=role.system_prompt,
            context_payload={
                "role_context": role_context.model_dump(mode="json"),
                "context_snapshot_hash": context_snapshot.snapshot_hash,
                "global_policy_hash": team.global_policy_hash,
                "role_registry_hash": team.role_registry_hash,
                "context_projector_hash": team.context_projector_hash,
                "consent_epoch": consent.consent_epoch,
                "consent_hash": consent.consent_hash,
                "repair_instruction": (
                    "Return one corrected JSON array matching the fixed role schema."
                    if work_item.attempt_no == 2
                    else "Return one JSON array matching the fixed role schema."
                ),
            },
            action_schema=output_schema,
            model_parameters={
                "temperature": 0,
                "max_output_tokens": self.model_config.max_output_tokens,
                "timeout_seconds": self.model_config.timeout_seconds,
                "response_format": {"type": "json_object"},
            },
            model_profile_hash=team.model_profile_hash,
            repair=work_item.attempt_no == 2,
        )
        request_bytes = canonical_request_bytes(request)
        try:
            assert_complete_request_within_limit(request, max_bytes=MAX_COMPLETE_REQUEST_BYTES)
        except ValueError as exc:
            raise AgentTeamWorkerError("MULTI_AGENT_BUDGET_EXCEEDED") from exc
        estimated_input_tokens = math.ceil(len(request_bytes) / 4)
        allowed_input_tokens = min(
            self.multi_agent_config.max_input_tokens,
            consent.max_input_tokens,
            role_context.budget_slice.reserved_input_tokens,
        )
        if estimated_input_tokens > allowed_input_tokens:
            raise AgentTeamWorkerError("MULTI_AGENT_BUDGET_EXCEEDED")
        return PreparedAgentTeamReview(
            team=team,
            work_item=work_item,
            context_snapshot_hash=context_snapshot.snapshot_hash,
            role_context=role_context,
            request=request,
            request_hash=hashlib.sha256(request_bytes).hexdigest(),
            request_bytes=len(request_bytes),
            estimated_input_tokens=estimated_input_tokens,
            repair_of_model_call_id=repair_of_model_call_id,
        )

    def run_review(self, prepared: PreparedAgentTeamReview) -> AgentTeamReviewResult:
        try:
            findings, metadata = self.adapter.review(
                request=prepared.request,
                reviewer_kind=prepared.work_item.reviewer_kind,
            )
        except StructuredModelCallError as exc:
            raise AgentTeamWorkerError(
                exc.code, prepared=prepared, metadata=exc.metadata
            ) from exc
        role = role_for(prepared.work_item.reviewer_kind)
        validated = self.finding_aggregator.aggregate(
            findings=findings,
            input_refs=prepared.role_context.evidence_refs,
            allowed_codes=role.allowed_finding_codes,
            required_safety=False,
        )
        if validated.rejected_codes:
            raise AgentTeamWorkerError(
                validated.rejected_codes[0], prepared=prepared, metadata=metadata
            )
        return AgentTeamReviewResult(
            findings=findings,
            request_hash=prepared.request_hash,
            request_bytes=prepared.request_bytes,
            metadata=metadata,
        )

    def _validate_binding(
        self,
        *,
        team: AgentTeamRecord,
        work_item: AgentTeamWorkItem,
        context_snapshot: AgentTeamContextSnapshot,
        role_context: AgentTeamRoleContext,
        consent: ProjectMultiAgentConsent,
        repair_of_model_call_id: str | None,
        now: datetime,
    ) -> None:
        if not self.multi_agent_config.enabled:
            raise AgentTeamWorkerError("MULTI_AGENT_DISABLED")
        if (
            not consent.enabled
            or consent.project_id != team.project_id
            or consent.consent_epoch != team.consent_epoch
            or consent.consent_hash != team.consent_hash
            or role_context.consent_epoch != consent.consent_epoch
            or role_context.consent_hash != consent.consent_hash
        ):
            raise AgentTeamWorkerError("MULTI_AGENT_CONSENT_INVALIDATED")
        if self.model_config.provider != "openai_compatible":
            raise AgentTeamWorkerError("AGENT_TEAM_PROVIDER_UNAVAILABLE")
        if self.model_config.incomplete_reason() is not None:
            raise AgentTeamWorkerError("AGENT_MODEL_CONFIG_INCOMPLETE")
        if (
            team.budget.max_model_calls > self.multi_agent_config.max_model_calls
            or team.budget.max_repairs > self.multi_agent_config.max_repairs_per_role * 3
            or team.budget.reserved_input_tokens > self.multi_agent_config.max_input_tokens
            or team.budget.max_model_calls > consent.max_model_calls
            or team.budget.reserved_input_tokens > consent.max_input_tokens
        ):
            raise AgentTeamWorkerError("MULTI_AGENT_BUDGET_EXCEEDED")
        if (
            work_item.deadline_at <= now
            or work_item.deadline_at > team.budget.deadline_at
            or role_context.deadline_at <= now
            or role_context.deadline_at > role_context.budget_slice.deadline_at
        ):
            raise AgentTeamWorkerError("TEAM_ROLE_CONTEXT_DEADLINE_EXPIRED")
        if (
            team.status not in {"READY", "RUNNING"}
            or team.role_registry_hash != role_registry_hash()
            or team.context_projector_hash != context_projector_hash()
            or work_item.status not in {"PENDING", "RETRY_READY"}
            or work_item.team_id != team.team_id
            or work_item.team_epoch != team.team_epoch
            or work_item.project_id != team.project_id
            or work_item.lifecycle_id != team.lifecycle_id
            or work_item.deadline_at <= now
            or work_item.deadline_at > team.budget.deadline_at
            or role_context.team_id != team.team_id
            or role_context.team_epoch != team.team_epoch
            or role_context.project_id != team.project_id
            or role_context.lifecycle_id != team.lifecycle_id
            or context_snapshot.snapshot_hash != team.snapshot_hash
            or context_snapshot.team_id != team.team_id
            or context_snapshot.team_epoch != team.team_epoch
            or context_snapshot.project_id != team.project_id
            or context_snapshot.lifecycle_id != team.lifecycle_id
            or context_snapshot.base_context_hash != role_context.base_context_hash
            or context_snapshot.role_context_hashes.get(work_item.reviewer_kind)
            != role_context.role_context_hash
            or context_snapshot.role_registry_hash != team.role_registry_hash
            or context_snapshot.context_projector_hash != team.context_projector_hash
            or context_snapshot.consent_epoch != consent.consent_epoch
            or context_snapshot.consent_hash != consent.consent_hash
            or role_context.reviewer_kind != work_item.reviewer_kind
            or role_context.role_id != work_item.role_id
            or role_context.model_profile_hash != team.model_profile_hash
            or team.global_policy_hash
            != multi_agent_policy_hash(self.multi_agent_config)
            or team.model_profile_hash
            != team_review_model_profile_hash(
                config=self.model_config,
                max_output_tokens=self.multi_agent_config.max_output_tokens_per_call,
            )
        ):
            raise AgentTeamWorkerError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        role = role_for(work_item.reviewer_kind)
        if (
            role_context.prompt_template_version != role.prompt_template_version
            or role_context.output_schema_version != role.output_schema_version
            or role_context.purpose != role.purpose
        ):
            raise AgentTeamWorkerError("TEAM_ROLE_REGISTRY_INVALID")
        if work_item.attempt_no == 1 and repair_of_model_call_id is not None:
            raise AgentTeamWorkerError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        if work_item.attempt_no == 1 and work_item.model_call_id is not None:
            raise AgentTeamWorkerError("AGENT_TEAM_CONTEXT_BINDING_INVALID")
        if work_item.attempt_no == 2 and (
            not repair_of_model_call_id
            or work_item.model_call_id != repair_of_model_call_id
        ):
            raise AgentTeamWorkerError("AGENT_TEAM_CONTEXT_BINDING_INVALID")

    @staticmethod
    def _role_output_schema(
        context: AgentTeamRoleContext, allowed_codes: tuple[str, ...]
    ) -> dict[str, object]:
        schema = team_review_output_schema()
        item_schema = schema["items"]
        if not isinstance(item_schema, dict):
            raise AgentTeamWorkerError("TEAM_ROLE_REGISTRY_INVALID")
        properties = item_schema.get("properties")
        if not isinstance(properties, dict):
            raise AgentTeamWorkerError("TEAM_ROLE_REGISTRY_INVALID")
        properties["reviewer_kind"] = {"const": context.reviewer_kind}
        properties["code"] = {"type": "string", "enum": list(allowed_codes)}
        input_refs = properties.get("input_refs")
        if isinstance(input_refs, dict):
            input_refs["items"] = {"type": "string", "enum": list(context.evidence_refs)}
        return schema
