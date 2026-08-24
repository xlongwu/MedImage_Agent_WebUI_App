"""Binding-aware, side-effect-free collection of immutable run observations."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail, ReviewedPlanRecord, RunLinkRecord
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.node_contract import CapabilityLevel, NodeContract
from src.backend.app.schemas.observation import (
    CapabilityObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    ObservationSourceRef,
    ScientificObservation,
)
from src.backend.app.schemas.recovery_attempt import RecoveryAttemptRecord
from src.backend.app.services.observation_adapters import adapt_observation_sources


class ObservationStore(Protocol):
    def get_project(self, project_id: str) -> ProjectDetail | None: ...
    def get_agent_lifecycle(self, lifecycle_id: str) -> AgentLifecycleRecord | None: ...
    def get_reviewed_plan(self, reviewed_plan_id: str) -> ReviewedPlanRecord | None: ...
    def get_run_link_by_run_id(self, project_id: str, run_id: str) -> RunLinkRecord | None: ...
    def get_execution_ticket(self, execution_ticket_id: str) -> ExecutionTicket | None: ...
    def add_observation(self, record: ObservationRecord) -> ObservationRecord: ...
    def get_observation(self, observation_id: str) -> ObservationRecord | None: ...
    def list_observations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
        run_id: str | None = None,
    ) -> list[ObservationRecord]: ...
    def get_recovery_attempt(
        self, recovery_attempt_id: str
    ) -> RecoveryAttemptRecord | None: ...
    def get_gateway_dispatch_by_ticket(self, execution_ticket_id: str): ...


_CAPABILITY_ORDER: tuple[CapabilityLevel, ...] = (
    "unavailable",
    "scaffolded",
    "metadata_only",
    "computed",
    "validated",
)


def _minimum_level(levels: list[CapabilityLevel]) -> CapabilityLevel:
    if not levels:
        return "unavailable"
    return min(levels, key=_CAPABILITY_ORDER.index)


def _lower_level(left: CapabilityLevel, right: CapabilityLevel) -> CapabilityLevel:
    return left if _CAPABILITY_ORDER.index(left) <= _CAPABILITY_ORDER.index(right) else right


def _canonical_artifact_type(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    aliases = {
        "fc_matrix": "fc_matrix",
        "functional_connectivity_matrix": "fc_matrix",
        "roi_atlas": "atlas",
        "bold_nifti": "bold_nifti",
    }
    return aliases.get(normalized, normalized)


def calculate_observation_hash(record: ObservationRecord | dict[str, object]) -> str:
    payload = (
        record.model_dump(mode="json")
        if isinstance(record, ObservationRecord)
        else dict(record)
    )
    payload.pop("observation_hash", None)
    return stable_hash(payload)


def _merge_recovery_evidence(
    facts,
    previous: ObservationRecord,
    *,
    target_subjects: set[str],
) -> None:
    """Carry forward untouched parent evidence and replace only retry targets."""
    facts.sources = list(
        {
            source.source_id: source
            for source in (*previous.sources, *facts.sources)
        }.values()
    )

    prior_nodes = [
        node
        for node in previous.nodes
        if not node.subject_id or node.subject_id not in target_subjects
    ]
    facts.nodes = list(
        {
            (node.node_id, node.subject_id, node.session_id): node
            for node in (*prior_nodes, *facts.nodes)
        }.values()
    )

    prior_artifacts = [
        artifact
        for artifact in previous.artifacts
        if not artifact.subject_id or artifact.subject_id not in target_subjects
    ]
    facts.artifacts = list(
        {
            artifact.artifact_id: artifact
            for artifact in (*prior_artifacts, *facts.artifacts)
        }.values()
    )
    facts.validations = list(
        {
            validation.validation_id: validation
            for validation in (*previous.validations, *facts.validations)
        }.values()
    )
    prior_log_facts = [
        fact
        for fact in previous.log_facts
        if not fact.subject_id or fact.subject_id not in target_subjects
    ]
    facts.log_facts = list(
        {
            fact.fact_id: fact
            for fact in (*prior_log_facts, *facts.log_facts)
        }.values()
    )

    available_source_types = {
        source.source_type
        for source in facts.sources
        if source.read_status == "ok"
    }
    missing_source_types = {
        "artifacts": {"artifact_discovery", "artifact_registry"},
        "node_states": {"node_state", "node_states"},
        "validations": {"validation"},
    }
    facts.missing_sources = [
        item
        for item in facts.missing_sources
        if not (
            item in missing_source_types
            and missing_source_types[item] & available_source_types
        )
    ]


class ObservationCollector:
    VERSION = "observation-collector-v1"

    def __init__(self, store: ObservationStore) -> None:
        self.store = store

    def _bindings(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
    ) -> tuple[
        ProjectDetail,
        AgentLifecycleRecord,
        ReviewedPlanRecord,
        RunLinkRecord,
        ExecutionTicket,
    ]:
        project = self.store.get_project(project_id)
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        if project is None:
            raise SafetyError("OBSERVATION_PROJECT_NOT_FOUND", code="OBSERVATION_PROJECT_NOT_FOUND")
        if lifecycle is None or lifecycle.project_id != project_id:
            raise SafetyError("OBSERVATION_LIFECYCLE_NOT_FOUND", code="OBSERVATION_LIFECYCLE_NOT_FOUND")
        if not lifecycle.reviewed_plan_id or not lifecycle.execution_ticket_id or not lifecycle.run_id:
            raise SafetyError("OBSERVATION_BINDING_INCOMPLETE", code="OBSERVATION_BINDING_INCOMPLETE")
        reviewed_plan = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id)
        ticket = self.store.get_execution_ticket(lifecycle.execution_ticket_id)
        run_link = self.store.get_run_link_by_run_id(project_id, lifecycle.run_id)
        if reviewed_plan is None or reviewed_plan.project_id != project_id:
            raise SafetyError("OBSERVATION_REVIEWED_PLAN_MISMATCH", code="OBSERVATION_REVIEWED_PLAN_MISMATCH")
        if ticket is None or ticket.project_id != project_id:
            raise SafetyError("OBSERVATION_TICKET_MISMATCH", code="OBSERVATION_TICKET_MISMATCH")
        if run_link is None or run_link.project_id != project_id:
            raise SafetyError("OBSERVATION_RUN_LINK_MISMATCH", code="OBSERVATION_RUN_LINK_MISMATCH")
        if (
            ticket.reviewed_plan_id != reviewed_plan.reviewed_plan_id
            or run_link.reviewed_plan_id != reviewed_plan.reviewed_plan_id
            or ticket.plan_hash != reviewed_plan.plan_hash
            or lifecycle.reviewed_plan_id != reviewed_plan.reviewed_plan_id
        ):
            raise SafetyError("OBSERVATION_PLAN_BINDING_DRIFT", code="OBSERVATION_PLAN_BINDING_DRIFT")
        if run_link.audit_id and run_link.audit_id != ticket.audit_id:
            raise SafetyError("OBSERVATION_AUDIT_BINDING_DRIFT", code="OBSERVATION_AUDIT_BINDING_DRIFT")
        if ticket.ticket_kind == "recovery_child":
            payload = run_link.payload if isinstance(run_link.payload, dict) else {}
            output_root = Path(ticket.output_roots[0]).resolve() if ticket.output_roots else None
            attempt_root = Path(str(payload.get("attempt_output_root") or "")).resolve()
            state_root = Path(str(payload.get("state_root") or "")).resolve()
            if (
                output_root is None
                or payload.get("recovery_attempt_id") != ticket.recovery_attempt_id
                or payload.get("parent_run_id") != ticket.parent_run_id
                or payload.get("parent_execution_ticket_id") != ticket.parent_execution_ticket_id
                or payload.get("output_namespace") != ticket.output_namespace
                or attempt_root != output_root
                or state_root != output_root / "work"
            ):
                raise SafetyError(
                    "OBSERVATION_RECOVERY_LINEAGE_DRIFT",
                    code="OBSERVATION_RECOVERY_LINEAGE_DRIFT",
                )
        goal_contract = reviewed_plan.payload.get("goal_contract")
        if isinstance(goal_contract, dict):
            goal_hash = str(goal_contract.get("goal_contract_hash") or "")
            goal_id = str(goal_contract.get("goal_contract_id") or "")
            if (
                not goal_hash
                or ticket.goal_contract_hash != goal_hash
                or (lifecycle.goal_contract_hash and lifecycle.goal_contract_hash != goal_hash)
                or (lifecycle.goal_contract_id and lifecycle.goal_contract_id != goal_id)
            ):
                raise SafetyError("OBSERVATION_GOAL_BINDING_DRIFT", code="OBSERVATION_GOAL_BINDING_DRIFT")
        if ticket.status != "consumed":
            raise SafetyError("OBSERVATION_TICKET_NOT_CONSUMED", code="OBSERVATION_TICKET_NOT_CONSUMED")
        return project, lifecycle, reviewed_plan, run_link, ticket

    @staticmethod
    def _contract_sources(
        ticket: ExecutionTicket,
        *,
        collected_at: datetime,
    ) -> tuple[list[NodeContract], list[ObservationSourceRef], list[str]]:
        contracts: list[NodeContract] = []
        sources: list[ObservationSourceRef] = []
        conflicts: list[str] = []
        ticket_versions = dict(ticket.contract_versions)
        for node_id in ticket.approved_node_ids:
            try:
                contract = get_node_contract(node_id)
            except SafetyError:
                conflicts.append(f"NODE_CONTRACT_MISSING:{node_id}")
                continue
            expected_version = ticket_versions.get(node_id)
            if expected_version != contract.contract_version:
                conflicts.append(f"NODE_CONTRACT_VERSION_DRIFT:{node_id}")
                continue
            contracts.append(contract)
            payload_hash = stable_hash(contract.model_dump(mode="json"))
            sources.append(
                ObservationSourceRef(
                    source_id=f"source_{stable_hash({'node_id': node_id, 'hash': payload_hash})[:20]}",
                    source_type="node_contract",
                    record_id=node_id,
                    content_hash=payload_hash,
                    read_status="ok",
                    observed_at=collected_at,
                    freshness="fresh",
                    redacted=True,
                )
            )
        return contracts, sources, conflicts

    @staticmethod
    def _capability(
        contracts: list[NodeContract],
        facts,
        *,
        required_artifact_types: set[str] | None = None,
    ) -> tuple[CapabilityObservation, ScientificObservation]:
        declared = _minimum_level([contract.capability_level for contract in contracts])
        if required_artifact_types is None:
            required_numerical = {
                _canonical_artifact_type(artifact.artifact_type)
                for contract in contracts
                for artifact in contract.output_schema
                if (artifact.required or artifact.reload_required) and (
                    artifact.reload_required or artifact.artifact_type in {
                        "alff_map",
                        "falff_map",
                        "reho_map",
                        "fc_matrix",
                        "fisher_z_matrix",
                        "roi_timeseries",
                        "filtered_bold",
                        "denoised_bold",
                    }
                )
            }
        else:
            required_numerical = {
                _canonical_artifact_type(value)
                for value in required_artifact_types
            }
        artifact_by_type = {
            artifact_type: [
                artifact
                for artifact in facts.artifacts
                if _canonical_artifact_type(artifact.artifact_type) == artifact_type
            ]
            for artifact_type in required_numerical
        }
        downgrade_reasons: list[str] = []
        limitation_flags = {
            flag
            for artifact in facts.artifacts
            for flag in artifact.limitation_flags
            if flag in {"simplified", "preview_only", "partial"}
        }
        limitation_flags.update(
            flag
            for artifact in facts.artifacts
            if _canonical_artifact_type(artifact.artifact_type)
            in required_numerical
            for flag in artifact.limitation_flags
            if flag == "metadata_only"
        )
        limitation_flags = sorted(limitation_flags)
        if facts.pipeline.status not in {"SUCCESS", "COMPLETED"}:
            observed: CapabilityLevel = "unavailable"
            downgrade_reasons.append("PIPELINE_NOT_SUCCESSFUL")
        elif not facts.pipeline.summary_consistent:
            observed = "scaffolded"
            downgrade_reasons.append("NODE_STATE_INCONSISTENT")
        elif required_numerical:
            missing = [kind for kind, values in artifact_by_type.items() if not values]
            invalid = [
                artifact.artifact_id
                for values in artifact_by_type.values()
                for artifact in values
                if not (
                    artifact.exists
                    and artifact.registration_status == "registered"
                    and artifact.reload_status == "passed"
                    and artifact.shape
                    and artifact.dtype
                    and artifact.checksum_sha256
                    and artifact.input_hashes
                    and artifact.parameter_hash
                    and artifact.provenance_id
                )
            ]
            if missing or invalid:
                observed = "metadata_only"
                downgrade_reasons.extend(f"REQUIRED_ARTIFACT_MISSING:{item}" for item in missing)
                downgrade_reasons.extend(f"ARTIFACT_INTEGRITY_FAILED:{item}" for item in invalid)
            elif (
                not limitation_flags
                and facts.validations
                and all(item.status == "passed" for item in facts.validations)
            ):
                observed = "validated"
            else:
                observed = "computed"
        else:
            observed = "metadata_only"
        defensible = _lower_level(declared, observed)
        if defensible != observed:
            downgrade_reasons.append("DECLARED_CAPABILITY_LOWER_THAN_OBSERVED")
        evidence_ids = tuple(
            dict.fromkeys(
                evidence
                for artifact in facts.artifacts
                for evidence in artifact.evidence_ids
            )
        )
        capability = CapabilityObservation(
            declared_level=declared,
            observed_level=observed,
            defensible_level=defensible,
            downgrade_reasons=tuple(dict.fromkeys(downgrade_reasons)),
            evidence_ids=evidence_ids,
        )
        scientific = ScientificObservation(
            status=defensible,
            limitation_flags=tuple(limitation_flags),
            backend_ids=tuple(sorted({contract.backend for contract in contracts})),
            validation_evidence_ids=tuple(
                evidence
                for validation in facts.validations
                for evidence in validation.evidence_ids
            ),
        )
        return capability, scientific

    @staticmethod
    def _goal_required_artifact_types(
        reviewed_plan: ReviewedPlanRecord,
    ) -> set[str] | None:
        payload = (
            reviewed_plan.payload.get("goal_contract")
            if isinstance(reviewed_plan.payload, dict)
            else None
        )
        if not isinstance(payload, dict):
            return None
        criteria = payload.get("criteria")
        if not isinstance(criteria, list):
            return None
        return {
            str(criterion.get("target"))
            for criterion in criteria
            if isinstance(criterion, dict)
            and criterion.get("criterion_type")
            in {
                "artifact_present",
                "artifact_reloadable",
                "artifact_registered",
            }
            and criterion.get("target")
        }

    def collect(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        previous_observation_id: str | None = None,
        recovery_attempt_id: str | None = None,
    ) -> ObservationRecord:
        project, lifecycle, reviewed_plan, run_link, ticket = self._bindings(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
        )
        collected_at = datetime.now(UTC)
        facts = adapt_observation_sources(project, run_link, collected_at=collected_at)
        contracts, contract_sources, contract_conflicts = self._contract_sources(
            ticket,
            collected_at=collected_at,
        )
        facts.sources.extend(contract_sources)
        facts.conflicts.extend(contract_conflicts)
        if not contract_sources:
            facts.missing_sources.append("node_contracts")
        prior = previous_observation_id
        previous = None
        if prior is not None:
            previous = self.store.get_observation(prior)
            if (
                previous is None
                or previous.bindings.project_id != project_id
                or previous.bindings.lifecycle_id != lifecycle_id
            ):
                raise SafetyError(
                    "OBSERVATION_PREVIOUS_BINDING_MISMATCH",
                    code="OBSERVATION_PREVIOUS_BINDING_MISMATCH",
                )
        if recovery_attempt_id is not None:
            attempt = self.store.get_recovery_attempt(recovery_attempt_id)
            if (
                previous is None
                or attempt is None
                or attempt.project_id != project_id
                or attempt.lifecycle_id != lifecycle_id
            ):
                raise SafetyError(
                    "OBSERVATION_RECOVERY_EVIDENCE_BINDING_MISMATCH",
                    code="OBSERVATION_RECOVERY_EVIDENCE_BINDING_MISMATCH",
                )
            _merge_recovery_evidence(
                facts,
                previous,
                target_subjects=set(attempt.target_subject_ids),
            )
        if facts.pipeline.nodes_total == 0 and not facts.nodes and facts.pipeline.status != "UNKNOWN":
            facts.pipeline = facts.pipeline.model_copy(update={"summary_consistent": True, "active_nodes": 0})
        capability, scientific = self._capability(
            contracts,
            facts,
            required_artifact_types=self._goal_required_artifact_types(
                reviewed_plan
            ),
        )
        if facts.conflicts or facts.blocking_facts:
            completeness_status = "invalid"
        elif facts.missing_sources:
            completeness_status = "partial"
        else:
            completeness_status = "complete"
        if prior is None:
            existing = self.store.list_observations(
                project_id,
                lifecycle_id=lifecycle_id,
                run_id=run_link.run_id,
            )
            prior = existing[0].observation_id if existing else None
        goal_contract = reviewed_plan.payload.get("goal_contract")
        goal_contract_id = None
        goal_contract_hash = None
        if isinstance(goal_contract, dict):
            goal_contract_id = str(goal_contract.get("goal_contract_id") or "") or None
            goal_contract_hash = str(goal_contract.get("goal_contract_hash") or "") or None
        dispatch = self.store.get_gateway_dispatch_by_ticket(ticket.execution_ticket_id)
        if dispatch is None or dispatch.run_id != run_link.run_id:
            raise SafetyError(
                "OBSERVATION_DISPATCH_BINDING_MISSING",
                code="OBSERVATION_DISPATCH_BINDING_MISSING",
            )
        payload = {
            "observation_id": f"observation_{uuid4().hex}",
            "schema_version": 1,
            "collector_version": self.VERSION,
            "bindings": ObservationBindings(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                reviewed_plan_id=reviewed_plan.reviewed_plan_id,
                plan_hash=reviewed_plan.plan_hash,
                goal_contract_id=goal_contract_id,
                goal_contract_hash=goal_contract_hash,
                run_id=run_link.run_id,
                execution_ticket_id=ticket.execution_ticket_id,
                dispatch_id=dispatch.dispatch_id,
                recovery_attempt_id=recovery_attempt_id,
            ),
            "collected_at": collected_at,
            "sources": tuple(facts.sources),
            "pipeline": facts.pipeline,
            "nodes": tuple(facts.nodes),
            "artifacts": tuple(facts.artifacts),
            "validations": tuple(facts.validations),
            "log_facts": tuple(facts.log_facts),
            "capability": capability,
            "scientific": scientific,
            "completeness": ObservationCompleteness(
                status=completeness_status,
                missing_sources=tuple(dict.fromkeys(facts.missing_sources)),
                conflicts=tuple(dict.fromkeys(facts.conflicts)),
                blocking_facts=tuple(dict.fromkeys(facts.blocking_facts)),
            ),
            "previous_observation_id": prior,
            "observation_hash": "pending",
            "extensions": {},
        }
        record = ObservationRecord(**payload)
        record = record.model_copy(update={"observation_hash": calculate_observation_hash(record)})
        try:
            return self.store.add_observation(record)
        except Exception as exc:
            raise StateStoreError(
                "OBSERVATION_PERSISTENCE_FAILED",
                details={"lifecycle_id": lifecycle_id, "run_id": run_link.run_id},
            ) from exc
