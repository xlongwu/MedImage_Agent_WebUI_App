"""Pure validation and deterministic aggregation of read-only review findings."""

from __future__ import annotations

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field

from src.backend.app.schemas.agent_eval import AgentReviewFinding

_ROLE_ORDER = {"safety": 0, "science": 1, "completeness": 2}
_SEVERITY_ORDER = {"blocking": 0, "warning": 1}
_FORBIDDEN = re.compile(r"\b(command|shell|path|approval|approve|ticket|execute|execution|dispatch|runner)\b|命令|路径|审批|票据|执行|分派", re.IGNORECASE)


class AgentReviewAggregation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: tuple[AgentReviewFinding, ...] = ()
    rejected_codes: tuple[str, ...] = ()
    conflict_codes: tuple[str, ...] = ()
    safety_reviewer_present: bool
    advisory_hash: str = Field(min_length=64, max_length=64)


class AgentReviewFindingAggregator:
    """No model, store, planner or execution dependency is permitted here."""

    def aggregate(self, *, findings: tuple[AgentReviewFinding, ...], input_refs: tuple[str, ...], allowed_codes: tuple[str, ...], required_safety: bool = True) -> AgentReviewAggregation:
        rejected: list[str] = []
        valid: list[AgentReviewFinding] = []
        for finding in findings:
            if finding.code not in allowed_codes:
                rejected.append("MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN")
            elif not set(finding.input_refs).issubset(input_refs):
                rejected.append("MULTI_AGENT_REVIEW_INPUT_REF_INVALID")
            elif finding.suggested_change and _FORBIDDEN.search(finding.suggested_change):
                rejected.append("MULTI_AGENT_REVIEW_FORBIDDEN_SUGGESTION")
            else:
                valid.append(finding)
        deduped: dict[tuple[str, str, tuple[str, ...]], AgentReviewFinding] = {}
        for finding in valid:
            key = (finding.code, finding.severity, tuple(sorted(finding.input_refs)))
            existing = deduped.get(key)
            if existing is None or _ROLE_ORDER[finding.reviewer_kind] < _ROLE_ORDER[existing.reviewer_kind]:
                deduped[key] = finding
        accepted = tuple(sorted(deduped.values(), key=lambda item: (_SEVERITY_ORDER[item.severity], item.code, _ROLE_ORDER[item.reviewer_kind], item.input_refs)))
        by_code: dict[str, set[str]] = {}
        for finding in accepted:
            by_code.setdefault(finding.code, set()).add(finding.severity)
        conflicts = tuple(sorted(code for code, severities in by_code.items() if len(severities) > 1))
        safety_present = any(item.reviewer_kind == "safety" for item in accepted)
        if required_safety and not safety_present:
            rejected.append("MULTI_AGENT_REVIEW_SAFETY_REQUIRED")
        identity = {
            "accepted": [item.model_dump(mode="json", exclude={"suggested_change"}) for item in accepted],
            "rejected": sorted(set(rejected)),
            "conflicts": conflicts,
            "safety_reviewer_present": safety_present,
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        return AgentReviewAggregation(accepted=accepted, rejected_codes=tuple(sorted(set(rejected))), conflict_codes=conflicts, safety_reviewer_present=safety_present, advisory_hash=digest)


def aggregation_policy_hash() -> str:
    return hashlib.sha256(b"agent-review-finding-aggregator-v1:code-severity-refs").hexdigest()
