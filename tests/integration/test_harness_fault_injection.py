"""Phase-six offline fault injection against the real isolated Harness stack."""

from __future__ import annotations

from pathlib import Path

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import DefaultAgentModelAdapter
from src.backend.app.schemas.agent_eval import AgentEvalManifest
from src.backend.app.services.agent_evaluation_runner import AgentEvaluationRunner

_FAULT_CASE_IDS = frozenset({
    "provider-failure-en",
    "invalid-action-zh",
    "invalid-json-en",
    "invalid-action-type-zh",
    "provider-timeout-en",
    "missing-api-key-zh",
    "unknown-call-outcome-en",
    "duplicate-command-en",
    "restart-recovery-zh",
    "approval-drift-en",
    "unsafe-path-zh",
    "context-cross-project-zh",
})


def test_offline_fault_injection_matrix_stops_safely_without_execution() -> None:
    manifest_path = Path(__file__).parents[1] / "fixtures" / "agent_eval" / "v2" / "manifest.json"
    manifest = AgentEvalManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))

    assert {case.case_id for case in manifest.cases if case.fault_injection} == _FAULT_CASE_IDS
    report = AgentEvaluationRunner().run_manifest(
        manifest=manifest,
        model_adapter=DefaultAgentModelAdapter(config=AgentModelRuntimeConfig()),
    )
    by_id = {result.case_id: result for result in report.results}
    injected = [by_id[case_id] for case_id in _FAULT_CASE_IDS]

    assert report.gate_passed
    assert report.metrics["fault_injection_case_count"] == len(_FAULT_CASE_IDS)
    assert report.metrics["fault_injection_pass_rate"] == 1.0
    assert all(result.passed for result in injected)
    assert all(not result.forbidden_calls_observed for result in injected)
    assert by_id["unknown-call-outcome-en"].outcome.duplicate_side_effect_observed is False
    assert by_id["approval-drift-en"].outcome.stale_or_cross_project_blocked is True
    assert by_id["unsafe-path-zh"].outcome.unsafe_action_rejected is True
    assert by_id["context-cross-project-zh"].outcome.context_cross_project_blocked is True
