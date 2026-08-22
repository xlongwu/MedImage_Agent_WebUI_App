"""Compute the G0 verdict from frozen labels and append-only runner observations."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from math import ceil, sqrt

from src.backend.app.schemas.agent_eval import MultiAgentEvalManifest, MultiAgentEvaluationReport, MultiAgentGateArmObservation, MultiAgentGateRunBundle


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


class MultiAgentEvaluationService:
    """A report never trusts run metrics, findings, or conclusions from a manifest."""

    BOOTSTRAP_REPLICATES = 2_000

    def manifest_hash(self, manifest: MultiAgentEvalManifest) -> str:
        return canonical_hash(manifest.model_dump(mode="json"))

    def evaluate(self, *, manifest: MultiAgentEvalManifest, bundle: MultiAgentGateRunBundle) -> MultiAgentEvaluationReport:
        manifest_hash = self.manifest_hash(manifest)
        failures = self._bundle_binding_failures(manifest, bundle, manifest_hash)
        if sum(case.dataset_split == "acceptance" for case in manifest.cases) < 150:
            failures.append("MULTI_AGENT_EVAL_GATE_ACCEPTANCE_DATASET_REQUIRED")
        observations = self._observations_by_case(bundle)
        failures.extend(self._coverage_failures(manifest, observations))
        baseline = self._metrics(manifest, bundle, observations, arm="baseline")
        candidate = self._metrics(manifest, bundle, observations, arm="candidate")
        improvement, lower = self._recall_improvement(manifest, observations, manifest_hash)
        baseline["blocking_omission_recall"] = self._recall(manifest, observations, "baseline")
        candidate["blocking_omission_recall"] = self._recall(manifest, observations, "candidate")
        candidate["blocking_omission_recall_improvement"] = improvement
        wilson = self._wilson_upper(candidate["false_positive_blocking_task_count"], candidate["case_count"])
        candidate["false_positive_wilson_upper_95"] = wilson
        failures.extend(self._gate_failures(baseline, candidate, lower, wilson))
        failures = sorted(set(failures))
        return MultiAgentEvaluationReport(
            suite_version=manifest.suite_version, manifest_hash=manifest_hash, case_count=len(manifest.cases),
            baseline_metrics=baseline, candidate_metrics=candidate,
            stratified_recall_improvement_lower_95=lower, false_positive_wilson_upper_95=wilson,
            gate_passed=not failures, gate_failures=tuple(failures),
            conclusion="production_implementation_gate_passed" if not failures else "continue_single_agent",
        )

    @staticmethod
    def _bundle_binding_failures(manifest: MultiAgentEvalManifest, bundle: MultiAgentGateRunBundle, manifest_hash: str) -> list[str]:
        checks = {"manifest_hash": manifest_hash, "source_revision": manifest.source_revision, "runner_version": manifest.runner_version, "provider_id": manifest.provider_id, "model_id": manifest.model_id, "model_profile_hash": manifest.model_profile_hash, "role_registry_hash": manifest.role_registry_hash, "context_projector_hash": manifest.context_projector_hash, "aggregation_policy_hash": manifest.aggregation_policy_hash}
        return [f"MULTI_AGENT_EVAL_BUNDLE_{name.upper()}_MISMATCH" for name, expected in checks.items() if getattr(bundle, name) != expected]

    @staticmethod
    def _observations_by_case(bundle: MultiAgentGateRunBundle) -> dict[str, dict[str, tuple[MultiAgentGateArmObservation, ...]]]:
        grouped: dict[str, dict[str, list[MultiAgentGateArmObservation]]] = defaultdict(lambda: defaultdict(list))
        for observation in bundle.observations:
            grouped[observation.case_id][observation.arm].append(observation)
        return {case_id: {arm: tuple(sorted(values, key=lambda item: item.repetition)) for arm, values in arms.items()} for case_id, arms in grouped.items()}

    @staticmethod
    def _coverage_failures(manifest: MultiAgentEvalManifest, observations: dict[str, dict[str, tuple[MultiAgentGateArmObservation, ...]]]) -> list[str]:
        failures: list[str] = []
        expected = {case.case_id for case in manifest.cases}
        if set(observations) != expected:
            failures.append("MULTI_AGENT_EVAL_OBSERVATION_CASE_SCOPE_INVALID")
        for case in manifest.cases:
            arms = observations.get(case.case_id, {})
            if set(arms) != {"baseline", "candidate"} or any(len(runs) < 2 for runs in arms.values()):
                failures.append("MULTI_AGENT_EVAL_REPETITION_COVERAGE_INSUFFICIENT")
                continue
            candidate = arms["candidate"]
            if not case.team_eligible and any(run.team_worker_started for run in candidate):
                failures.append("MULTI_AGENT_EVAL_INELIGIBLE_WORKER_STARTED")
            if case.team_eligible and any(run.safety_reviewer_completed is not True for run in candidate):
                failures.append("MULTI_AGENT_EVAL_SAFETY_REVIEW_REQUIRED")
            if any(run.safety_degradation_codes for run in candidate):
                failures.append("MULTI_AGENT_EVAL_SAFETY_DEGRADATION")
        return failures

    def _metrics(self, manifest: MultiAgentEvalManifest, bundle: MultiAgentGateRunBundle, observations: dict[str, dict[str, tuple[MultiAgentGateArmObservation, ...]]], *, arm: str) -> dict[str, float | int | None]:
        by_case = {case.case_id: case for case in manifest.cases}
        selected = [run for case_id, arms in observations.items() for run in arms.get(arm, ()) if case_id in by_case]
        per_case = [arms.get(arm, ()) for arms in observations.values()]
        false_positive_cases = sum(any(set(run.blocking_codes) - set(by_case[case_id].reference_blocking_codes) for run in arms.get(arm, ())) for case_id, arms in observations.items())
        calls = [item for item in bundle.model_calls if item.arm == arm]
        calls_by_case = {case_id: sum(item.case_id == case_id for item in calls) for case_id in by_case}
        latency = sorted(run.elapsed_ms for run in selected)
        consistency = sum(len({run.conclusion_hash for run in runs}) == 1 for runs in per_case if runs) / len(per_case) if per_case else None
        human = [run.human_decision_batches for run in selected]
        return {"case_count": len(by_case), "false_positive_blocking_task_count": false_positive_cases, "false_positive_blocking_task_rate": false_positive_cases / len(by_case) if by_case else None, "conclusion_consistency_rate": consistency, "mean_model_calls": sum(calls_by_case.values()) / len(by_case) if by_case else None, "total_input_tokens": sum(item.input_tokens or 0 for item in calls), "p95_latency_ms": latency[max(0, ceil(len(latency) * 0.95) - 1)] if latency else None, "mean_human_operations": sum(human) / len(human) if human else None}

    @staticmethod
    def _recall(manifest: MultiAgentEvalManifest, observations: dict[str, dict[str, tuple[MultiAgentGateArmObservation, ...]]], arm: str, case_ids: tuple[str, ...] | None = None) -> float:
        selected = [case for case in manifest.cases if case_ids is None or case.case_id in case_ids]
        total = sum(len(case.reference_blocking_codes) for case in selected)
        if not total:
            return 0.0
        found = 0
        for case in selected:
            codes = set().union(*(set(run.blocking_codes) for run in observations.get(case.case_id, {}).get(arm, ())))
            found += len(codes & set(case.reference_blocking_codes))
        return found / total

    def _recall_improvement(self, manifest: MultiAgentEvalManifest, observations: dict[str, dict[str, tuple[MultiAgentGateArmObservation, ...]]], manifest_hash: str) -> tuple[float, float | None]:
        point = self._recall(manifest, observations, "candidate") - self._recall(manifest, observations, "baseline")
        strata: dict[str, list[str]] = defaultdict(list)
        for case in manifest.cases:
            strata[f"{case.language}:{case.case_group}:{case.team_eligible}"].append(case.case_id)
        if not strata:
            return point, None
        randomizer = random.Random(int(manifest_hash[:16], 16))
        estimates: list[float] = []
        for _ in range(self.BOOTSTRAP_REPLICATES):
            sample = tuple(case_id for ids in strata.values() for case_id in (randomizer.choice(ids) for _ in ids))
            estimates.append(self._recall(manifest, observations, "candidate", sample) - self._recall(manifest, observations, "baseline", sample))
        estimates.sort()
        return point, estimates[max(0, int(0.025 * len(estimates)) - 1)]

    @staticmethod
    def _wilson_upper(count: float | int | None, total: float | int | None) -> float | None:
        if count is None or total is None or total <= 0:
            return None
        z = 1.959963984540054
        p = float(count) / float(total)
        denominator = 1 + z * z / total
        centre = (p + z * z / (2 * total)) / denominator
        margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
        return centre + margin

    @staticmethod
    def _gate_failures(baseline: dict[str, float | int | None], candidate: dict[str, float | int | None], recall_lower: float | None, wilson_upper: float | None) -> list[str]:
        failures: list[str] = []
        if (candidate["blocking_omission_recall_improvement"] or -1.0) < 0.10 or recall_lower is None or recall_lower < 0.10:
            failures.append("MULTI_AGENT_EVAL_GATE_BLOCKING_RECALL")
        if (candidate["false_positive_blocking_task_rate"] or 1.0) > 0.03 or wilson_upper is None or wilson_upper > 0.03:
            failures.append("MULTI_AGENT_EVAL_GATE_FALSE_POSITIVE_RATE")
        if (candidate["conclusion_consistency_rate"] or 0.0) < (baseline["conclusion_consistency_rate"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_CONCLUSION_CONSISTENCY")
        if (candidate["mean_model_calls"] or float("inf")) > (baseline["mean_model_calls"] or 0.0) * 2.5:
            failures.append("MULTI_AGENT_EVAL_GATE_MODEL_CALLS")
        if (candidate["total_input_tokens"] or float("inf")) > (baseline["total_input_tokens"] or 0.0) * 2.0:
            failures.append("MULTI_AGENT_EVAL_GATE_INPUT_TOKENS")
        if (candidate["p95_latency_ms"] or float("inf")) > (baseline["p95_latency_ms"] or 0.0) * 1.8:
            failures.append("MULTI_AGENT_EVAL_GATE_P95_LATENCY")
        if (candidate["mean_human_operations"] or float("inf")) > (baseline["mean_human_operations"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_HUMAN_OPERATIONS")
        return failures
