"""Read-only collection of bounded, structured project evidence for Agent planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_evidence import (
    EvidenceFact,
    EvidenceSnapshot,
    EvidenceSourceRef,
    EvidenceType,
    EvidenceWarning,
)
from src.backend.app.schemas.memory import MemoryContext


class AgentEvidenceService:
    """Collect allowlisted records without opening source files or log bodies."""

    DEFAULT_TYPES: tuple[EvidenceType, ...] = (
        "project", "dataset", "artifacts", "plans", "runs", "observations", "memory", "capabilities"
    )

    def __init__(self, store) -> None:
        self.store = store

    def build_snapshot(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        requested_types: Iterable[EvidenceType] | None = None,
        memory_context: MemoryContext | None = None,
    ) -> EvidenceSnapshot:
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        project = self.store.get_project(project_id)
        if lifecycle is None or lifecycle.project_id != project_id or project is None:
            raise SafetyError("AGENT_EVIDENCE_BINDING_INVALID", code="AGENT_EVIDENCE_BINDING_INVALID")
        requested = tuple(dict.fromkeys(requested_types or self.DEFAULT_TYPES))
        if not requested or any(item not in self.DEFAULT_TYPES for item in requested):
            raise SafetyError("AGENT_EVIDENCE_TYPE_INVALID", code="AGENT_EVIDENCE_TYPE_INVALID")

        facts: list[EvidenceFact] = []
        missing: list[str] = []
        warnings: list[EvidenceWarning] = []
        refs: list[EvidenceSourceRef] = []
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        project_ref = EvidenceSourceRef(source_type="project", source_id=project_id)

        if "project" in requested:
            facts.extend((
                EvidenceFact(key="dataset_type", value=project.modality, source_refs=(project_ref,)),
                EvidenceFact(key="subject_count", value=project.subjects_count, source_refs=(project_ref,)),
            ))
            refs.append(project_ref)
            metadata_subjects = metadata.get("subject_count")
            if isinstance(metadata_subjects, int) and metadata_subjects != project.subjects_count:
                warnings.append(EvidenceWarning(
                    code="EVIDENCE_SUBJECT_COUNT_CONFLICT",
                    summary="Project metadata subject count conflicts with the authoritative project record.",
                    source_refs=(project_ref,),
                ))

        if "dataset" in requested:
            dataset = self.store.get_dataset_summary(project_id) if hasattr(self.store, "get_dataset_summary") else None
            if dataset is None:
                missing.append("dataset_summary")
            else:
                dataset_ref = EvidenceSourceRef(source_type="dataset_summary", source_id=project_id)
                facts.extend((
                    EvidenceFact(key="dataset_health", value=str(dataset.health_status), source_refs=(dataset_ref,)),
                    EvidenceFact(key="dataset_subject_count", value=dataset.subjects, source_refs=(dataset_ref,)),
                ))
                refs.append(dataset_ref)

        if "artifacts" in requested:
            imports = self.store.list_import_records(project_id) if hasattr(self.store, "list_import_records") else []
            facts.append(EvidenceFact(
                key="registered_input_count", value=len(imports),
                source_refs=tuple(EvidenceSourceRef(source_type="dataset_import", source_id=str(item.get("dataset_id") or "unknown")) for item in imports[:64]),
            ))
            if not imports:
                missing.append("registered_input")

        if "plans" in requested:
            plans = self.store.list_reviewed_plans(project_id) if hasattr(self.store, "list_reviewed_plans") else []
            plan_refs = tuple(EvidenceSourceRef(source_type="reviewed_plan", source_id=plan.reviewed_plan_id, source_hash=plan.plan_hash) for plan in plans[:32])
            facts.append(EvidenceFact(key="reviewed_plan_count", value=len(plans), source_refs=plan_refs))
            refs.extend(plan_refs)

        if "runs" in requested:
            runs = self.store.list_run_links(project_id) if hasattr(self.store, "list_run_links") else []
            run_refs = tuple(EvidenceSourceRef(source_type="run", source_id=run.run_id) for run in runs[:32])
            facts.append(EvidenceFact(key="run_count", value=len(runs), source_refs=run_refs))
            refs.extend(run_refs)

        if "observations" in requested:
            observations = self.store.list_observations(project_id, lifecycle_id=lifecycle_id) if hasattr(self.store, "list_observations") else []
            evaluations = self.store.list_goal_evaluations(project_id, lifecycle_id=lifecycle_id) if hasattr(self.store, "list_goal_evaluations") else []
            observation_refs = tuple(EvidenceSourceRef(source_type="observation", source_id=item.observation_id, source_hash=item.observation_hash) for item in observations[:16])
            evaluation_refs = tuple(EvidenceSourceRef(source_type="goal_evaluation", source_id=item.goal_evaluation_id, source_hash=item.goal_evaluation_hash) for item in evaluations[:16])
            facts.extend((
                EvidenceFact(key="observation_count", value=len(observations), source_refs=observation_refs),
                EvidenceFact(key="goal_evaluation_count", value=len(evaluations), source_refs=evaluation_refs),
            ))
            refs.extend((*observation_refs, *evaluation_refs))

        if "memory" in requested:
            if memory_context is None:
                facts.append(EvidenceFact(key="memory_suggestion_count", value=0))
            else:
                memory_refs = tuple(EvidenceSourceRef(source_type="memory_suggestion", source_id=item.memory_id, source_hash=item.revision_hash) for item in memory_context.decision_suggestions[:16])
                facts.append(EvidenceFact(key="memory_suggestion_count", value=len(memory_refs), source_refs=memory_refs))
                refs.extend(memory_refs)

        if "capabilities" in requested:
            provider = metadata.get("agent_planner_provider")
            if isinstance(provider, str) and provider:
                facts.append(EvidenceFact(key="planner_provider", value=provider, source_refs=(project_ref,)))
            else:
                missing.append("planner_provider")

        identity = {
            "schema_version": 1, "project_id": project_id, "lifecycle_id": lifecycle_id,
            "requested_types": requested,
            "facts": [item.model_dump(mode="json") for item in facts],
            "missing": sorted(set(missing)),
            "warnings": [item.model_dump(mode="json") for item in warnings],
            "source_refs": [item.model_dump(mode="json") for item in refs],
        }
        snapshot = EvidenceSnapshot(
            snapshot_hash=stable_hash(identity), project_id=project_id, lifecycle_id=lifecycle_id,
            requested_types=requested, facts=tuple(facts), missing=tuple(sorted(set(missing))),
            warnings=tuple(warnings), source_refs=tuple(refs),
        )
        if hasattr(self.store, "add_agent_evidence_snapshot"):
            self.store.add_agent_evidence_snapshot(snapshot)
        return snapshot

    @staticmethod
    def project_relative_ref(project_dir: str | None, value: str) -> str:
        """Convert known project paths to a stable relative ref, never read them."""
        if not project_dir or not value:
            return value
        try:
            return str(Path(value).resolve().relative_to(Path(project_dir).resolve()))
        except (OSError, ValueError):
            return "external-path-redacted"
