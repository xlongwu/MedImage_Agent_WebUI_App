from __future__ import annotations

from hashlib import sha256

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.agent_eval import MultiAgentEvalManifest, MultiAgentGateArmObservation, MultiAgentGateModelCallRecord, MultiAgentGateRunBundle
from src.backend.app.services.agent_review_context_projector import AgentReviewContextProjector, context_projector_hash
from src.backend.app.services.agent_review_finding_aggregator import AgentReviewFindingAggregator, aggregation_policy_hash
from src.backend.app.services.agent_review_role_registry import role_registry_hash
from src.backend.app.services.multi_agent_evaluation_service import MultiAgentEvaluationService


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _manifest_payload(*, size: int = 30, split: str = "pilot") -> dict:
    cases = []
    for index in range(size):
        group = ("team_eligible", "team_ineligible", "adversarial_failure")[index % 3]
        eligible = group != "team_ineligible"
        cases.append({
            "case_id": f"case-{index}", "source_kind": "trace_replay_redacted", "source_ref_hash": _hash(f"source-{index}"),
            "redaction_review_ids": [f"redactor-{index}"], "label_review_ids": [f"label-a-{index}", f"label-b-{index}"],
            "dataset_split": split, "case_group": group, "team_eligible": eligible,
            "goal_summary": "Review a redacted rs-fMRI planning request.", "language": "en" if index % 2 else "zh-CN",
            "scenarios": ["plan_only" if index == 0 else "qc"], "frozen_context_hash": _hash(f"context-{index}"),
            "input_refs": ["evidence:goal:present"], "reference_blocking_codes": ["MISSING_PREREQUISITE"],
            "reference_blocking_refs": {"MISSING_PREREQUISITE": ["evidence:goal:present"]}, "prohibited_blocking_codes": [],
        })
    required = ["plan_only", "missing_prerequisite", "alff_falff", "reho", "motion", "qc", "unsupported_goal", "environment_unavailable", "unsafe_write_root", "invalid_model_action"]
    for scenario, case in zip(required, cases):
        case["scenarios"] = [scenario]
    return {
        "schema_version": 3, "suite_version": "g0-test-v1", "source_revision": "abcdef0", "runner_version": "multi-agent-gate-runner-v1", "redaction_policy_version": "g0-redaction-v1",
        "role_registry_hash": role_registry_hash(), "context_projector_hash": context_projector_hash(), "aggregation_policy_hash": aggregation_policy_hash(),
        "model_profile_hash": _hash("profile"), "provider_id": "approved-provider", "model_id": "approved-model", "allowed_finding_codes": ["MISSING_PREREQUISITE"], "cases": cases,
    }


def _bundle(manifest: MultiAgentEvalManifest) -> MultiAgentGateRunBundle:
    service = MultiAgentEvaluationService()
    observations = []
    calls = []
    for case in manifest.cases:
        for arm in ("baseline", "candidate"):
            for repetition in (1, 2):
                observations.append(MultiAgentGateArmObservation(
                    case_id=case.case_id, arm=arm, repetition=repetition, status="safe_stop", conclusion_hash=_hash(f"{case.case_id}:{arm}"),
                    blocking_codes=() if arm == "baseline" else case.reference_blocking_codes,
                    human_decision_batches=1, lifecycle_id_hash=_hash(f"lifecycle:{case.case_id}:{arm}:{repetition}"),
                    team_worker_started=arm == "candidate" and case.team_eligible,
                    safety_reviewer_completed=True if arm == "candidate" and case.team_eligible else None, elapsed_ms=100 if arm == "baseline" else 150,
                ))
                calls.append(MultiAgentGateModelCallRecord(
                    gate_run_id="g0-test", case_id=case.case_id, arm=arm, repetition=repetition, role_id=None,
                    source_revision=manifest.source_revision, source_tree_hash=_hash("tree"), runner_version=manifest.runner_version,
                    provider_id=manifest.provider_id, model_id=manifest.model_id, model_profile_hash=manifest.model_profile_hash,
                    role_registry_hash=manifest.role_registry_hash, prompt_schema_policy_hash=_hash("prompt"), context_hash=case.frozen_context_hash,
                    request_hash=_hash(f"request:{case.case_id}:{arm}:{repetition}"), status="completed", response_hash=_hash(f"response:{case.case_id}:{arm}:{repetition}"),
                    input_tokens=100, output_tokens=10, latency_ms=50, started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:00:01Z",
                ))
    return MultiAgentGateRunBundle(
        gate_run_id="g0-test", manifest_hash=service.manifest_hash(manifest), source_revision=manifest.source_revision, source_tree_hash=_hash("tree"), runner_version=manifest.runner_version,
        provider_id=manifest.provider_id, model_id=manifest.model_id, model_profile_hash=manifest.model_profile_hash, role_registry_hash=manifest.role_registry_hash,
        context_projector_hash=manifest.context_projector_hash, aggregation_policy_hash=manifest.aggregation_policy_hash,
        observations=tuple(observations), model_calls=tuple(calls),
    )


def test_manifest_contains_only_real_redacted_inputs_and_human_labels() -> None:
    manifest = MultiAgentEvalManifest.model_validate(_manifest_payload())

    assert manifest.schema_version == 3
    assert manifest.cases[0].source_kind == "trace_replay_redacted"
    with pytest.raises(ValidationError):
        MultiAgentEvalManifest.model_validate({**_manifest_payload(), "baseline": {"call_count": 1}})
    bad = _manifest_payload()
    bad["cases"][0]["goal_summary"] = "read C:\\research\\rawdata"
    with pytest.raises(ValidationError, match="REDACTION"):
        MultiAgentEvalManifest.model_validate(bad)
    with pytest.raises(ValidationError, match="ACCEPTANCE_CASE_COUNT"):
        MultiAgentEvalManifest.model_validate(_manifest_payload(split="acceptance"))


def test_pilot_bundle_is_reported_but_cannot_open_the_production_gate() -> None:
    manifest = MultiAgentEvalManifest.model_validate(_manifest_payload())
    report = MultiAgentEvaluationService().evaluate(manifest=manifest, bundle=_bundle(manifest))

    assert report.gate_passed is False
    assert report.conclusion == "continue_single_agent"
    assert "MULTI_AGENT_EVAL_GATE_ACCEPTANCE_DATASET_REQUIRED" in report.gate_failures


def test_projector_and_aggregator_are_pure_and_reject_untrusted_output() -> None:
    manifest = MultiAgentEvalManifest.model_validate(_manifest_payload())
    context = AgentReviewContextProjector().project(case=manifest.cases[0], reviewer_kind="safety")
    aggregation = AgentReviewFindingAggregator().aggregate(findings=(), input_refs=context.evidence_refs, allowed_codes=manifest.allowed_finding_codes)

    assert context.role_id == "safety_reviewer.v1"
    assert aggregation.safety_reviewer_present is False
    assert aggregation.rejected_codes == ("MULTI_AGENT_REVIEW_SAFETY_REQUIRED",)


def test_bundle_hash_drift_fails_closed() -> None:
    manifest = MultiAgentEvalManifest.model_validate(_manifest_payload())
    bundle = _bundle(manifest).model_copy(update={"model_id": "different-model"})
    report = MultiAgentEvaluationService().evaluate(manifest=manifest, bundle=bundle)

    assert "MULTI_AGENT_EVAL_BUNDLE_MODEL_ID_MISMATCH" in report.gate_failures
