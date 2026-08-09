"""Consent-gated retrieval and typed planner context construction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.memory import (
    MemoryContext,
    MemoryDecisionSuggestion,
    MemoryEvidenceRef,
    MemoryItem,
)
from src.backend.app.services.memory_repository import MemoryRepositoryError

RETRIEVAL_POLICY_VERSION = "memory-retrieval-v1"


@dataclass(frozen=True)
class MemoryRetrievalResult:
    items: tuple[MemoryItem, ...]
    warnings: tuple[str, ...]
    omitted_count: int
    used_bytes: int
    status: str


class MemoryRetrievalService:
    """Build a bounded read model without granting memory execution authority."""

    def __init__(self, *, repository, project_store, config) -> None:
        self.repository = repository
        self.project_store = project_store
        self.config = config

    def retrieve(self, *, project_id: str, query: str) -> MemoryRetrievalResult:
        consent = self.project_store.get_memory_consent(project_id)
        if not (
            self.config.enabled
            and self.config.use_enabled
            and bool(consent.get("use_enabled"))
        ):
            return MemoryRetrievalResult(
                items=(), warnings=(), omitted_count=0, used_bytes=0, status="disabled"
            )
        health = self.operational_health(project_id=project_id, consent=consent)
        if health["status"] == "failure":
            raise MemoryRepositoryError(str(health["degraded_reason"] or "MEMORY_STORE_UNHEALTHY"))

        forgotten = self._forgotten_generations(project_id)
        selected: list[MemoryItem] = []
        warnings: list[str] = list(health["warning_codes"])
        omitted = 0
        used_bytes = 0
        for item, _score in self.repository.retrieve_active_items(
            project_id=project_id, query=query, limit=200
        ):
            if item.generation <= forgotten.get(item.canonical_key, -1):
                omitted += 1
                continue
            stale = self._staleness(item)
            if stale and item.kind != "user_preference":
                omitted += 1
                continue
            if stale:
                warnings.append(f"MEMORY_SOURCE_STALE:{item.memory_id}")
            encoded = json.dumps(
                item.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if used_bytes + len(encoded) > self.config.max_context_bytes:
                omitted += 1
                continue
            used_bytes += len(encoded)
            selected.append(item)
        return MemoryRetrievalResult(
            items=tuple(selected),
            warnings=tuple(dict.fromkeys(warnings)),
            omitted_count=omitted,
            used_bytes=used_bytes,
            status="partial" if warnings or omitted else "enabled",
        )

    def operational_health(
        self, *, project_id: str, consent: dict[str, object] | None = None
    ) -> dict[str, Any]:
        consent = consent or self.project_store.get_memory_consent(project_id)
        master_enabled = bool(self.config.enabled)
        generation_available = master_enabled and bool(self.config.generation_enabled)
        use_available = master_enabled and bool(self.config.use_enabled)
        project_enabled = bool(consent.get("generate_enabled") or consent.get("use_enabled"))
        if not master_enabled:
            return self._health_payload(
                status="disabled",
                consent=consent,
                generation_available=False,
                use_available=False,
                degraded_reason="MEMORY_DISABLED",
            )
        try:
            store_health = self.repository.health_check()
        except Exception:
            store_health = {"ok": False, "error_code": "MEMORY_STORE_UNHEALTHY"}
        if store_health.get("ok") is not True:
            return self._health_payload(
                status="failure" if project_enabled else "disabled",
                consent=consent,
                generation_available=False,
                use_available=False,
                degraded_reason=str(
                    store_health.get("error_code") or "MEMORY_STORE_UNHEALTHY"
                ),
                store_healthy=False,
            )
        try:
            counts = self.repository.operational_counts(project_id=project_id)
            outbox_max = int(self.project_store.get_memory_outbox_max_sequence(project_id))
            watermark = self.repository.get_watermark(
                consumer="memory-phase1-v1", project_id=project_id
            )
            processed = int(watermark.get("source_sequence") or 0)
            forget_records = self.project_store.list_memory_forget_ledger(project_id)
            pending_forgets = sum(
                1
                for record in forget_records
                if (
                    (item := self.repository.get_item_by_canonical_key(
                        project_id=project_id,
                        canonical_key=str(record.get("canonical_key") or ""),
                    ))
                    is not None
                    and item.status != "forgotten"
                )
            )
        except Exception as exc:
            return self._health_payload(
                status="failure" if project_enabled else "disabled",
                consent=consent,
                generation_available=False,
                use_available=False,
                degraded_reason="MEMORY_OUTBOX_PREFLIGHT_FAILED",
                store_healthy=True,
                detail=type(exc).__name__,
            )
        lag = max(0, outbox_max - processed)
        warnings = []
        if lag:
            warnings.append("MEMORY_OUTBOX_LAG")
        if counts["retry_jobs"]:
            warnings.append("MEMORY_RETRY_PENDING")
        if counts["dead_letter_jobs"]:
            warnings.append("MEMORY_DEAD_LETTER_PRESENT")
        if counts["expired_leases"]:
            warnings.append("MEMORY_EXPIRED_LEASE_PRESENT")
        if pending_forgets:
            warnings.append("MEMORY_FORGET_RECONCILIATION_PENDING")
        status = "disabled" if not project_enabled else "partial" if warnings else "healthy"
        return self._health_payload(
            status=status,
            consent=consent,
            generation_available=generation_available,
            use_available=use_available,
            store_healthy=True,
            outbox_max_sequence=outbox_max,
            processed_outbox_sequence=processed,
            outbox_lag=lag,
            pending_forget_records=pending_forgets,
            warning_codes=tuple(warnings),
            last_forget_wal_truncate_at=store_health.get("last_forget_wal_truncate_at"),
            **counts,
        )

    @staticmethod
    def _health_payload(**values: Any) -> dict[str, Any]:
        base = {
            "status": "disabled",
            "generation_available": False,
            "use_available": False,
            "degraded_reason": None,
            "store_healthy": False,
            "outbox_max_sequence": 0,
            "processed_outbox_sequence": 0,
            "outbox_lag": 0,
            "retry_jobs": 0,
            "dead_letter_jobs": 0,
            "active_leases": 0,
            "expired_leases": 0,
            "pending_forget_records": 0,
            "last_forget_wal_truncate_at": None,
            "warning_codes": (),
        }
        base.update(values)
        return base

    def build_context(self, *, project_id: str, goal: str) -> MemoryContext:
        result = self.retrieve(project_id=project_id, query=goal)
        return self._build_context(project_id=project_id, result=result)

    def build_context_with_warnings(
        self, *, project_id: str, goal: str
    ) -> tuple[MemoryContext, tuple[str, ...]]:
        """Return the frozen context together with non-authoritative retrieval warnings."""

        result = self.retrieve(project_id=project_id, query=goal)
        return self._build_context(project_id=project_id, result=result), result.warnings

    def _build_context(
        self, *, project_id: str, result: MemoryRetrievalResult
    ) -> MemoryContext:
        suggestions: list[MemoryDecisionSuggestion] = []
        evidence: list[MemoryEvidenceRef] = []
        for item in result.items:
            revision = item.revision
            source_refs = tuple(source.source_ref for source in item.sources)
            if (
                item.kind == "project_decision"
                and revision.impact_class == "scientific"
                and revision.algorithm_id
                and revision.algorithm_version
                and revision.config_fingerprint
                and revision.confirmation_event_id
                and isinstance(revision.content.get("decision_kind"), str)
            ):
                suggestions.append(
                    MemoryDecisionSuggestion(
                        memory_id=item.memory_id,
                        revision_hash=revision.content_hash,
                        decision_kind=revision.content["decision_kind"],
                        typed_value=dict(revision.content),
                        algorithm_id=revision.algorithm_id,
                        algorithm_version=revision.algorithm_version,
                        config_fingerprint=revision.config_fingerprint,
                        applicability=dict(revision.applicability),
                        confirmation_event_id=revision.confirmation_event_id,
                        source_refs=source_refs,
                    )
                )
                continue
            for source_ref in source_refs or ("memory:no-source",):
                evidence.append(
                    MemoryEvidenceRef(
                        kind=item.kind,
                        memory_id=item.memory_id,
                        revision_hash=revision.content_hash,
                        source_ref=source_ref,
                        provenance_warning=(
                            "source_stale"
                            if f"MEMORY_SOURCE_STALE:{item.memory_id}" in result.warnings
                            else None
                        ),
                    )
                )
        identity = {
            "schema_version": "memory-context-v1",
            "retrieval_policy_version": RETRIEVAL_POLICY_VERSION,
            "project_id": project_id,
            "planner_constraints": {},
            "decision_suggestions": [
                item.model_dump(mode="json") for item in suggestions
            ],
            "evidence_refs": [item.model_dump(mode="json") for item in evidence],
            "omitted_count": result.omitted_count,
            "used_bytes": result.used_bytes,
            "status": result.status,
            "warning_codes": list(result.warnings),
        }
        return MemoryContext(
            **identity,
            context_hash=stable_hash(identity),
        )

    def _forgotten_generations(self, project_id: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for row in self.project_store.list_memory_forget_ledger(project_id):
            key = str(row.get("canonical_key") or "")
            values[key] = max(values.get(key, -1), int(row.get("generation") or 0))
        return values

    def _staleness(self, item: MemoryItem) -> bool:
        for source in item.sources:
            if source.source_trust_class == "explicit_user":
                continue
            projection = self.project_store.get_memory_source_projection(
                project_id=item.project_id,
                source_type=source.source_type,
                source_id=source.source_id,
            )
            if projection is None or projection.get("source_hash") != source.source_hash:
                return True
        return False
