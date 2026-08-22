from __future__ import annotations

from pathlib import Path

from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.agent_model_adapter import DefaultAgentModelAdapter
from src.backend.app.schemas.agent_eval import AgentEvalManifest
from src.backend.app.services.agent_evaluation_runner import AgentEvaluationRunner


def test_v2_runner_uses_isolated_real_lifecycle_records() -> None:
    manifest_path = Path(__file__).parents[1] / "fixtures" / "agent_eval" / "v2" / "manifest.json"
    manifest = AgentEvalManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    report = AgentEvaluationRunner().run_manifest(
        manifest=manifest,
        model_adapter=DefaultAgentModelAdapter(config=AgentModelRuntimeConfig()),
    )
    assert report.gate_passed
    assert len(report.results) == len(manifest.cases)
    assert all(item.passed and item.trace_hash for item in report.results)
    assert report.passed_case_count == report.case_count
    assert report.failed_case_count == 0
    assert report.gate_failures == ()
    assert all(item.outcome.case_id == item.case_id for item in report.results)
    assert all(not item.forbidden_calls_observed for item in report.results)
    by_id = {item.case_id: item for item in report.results}
    assert by_id["plan-only-zh"].final_state == "SUCCEEDED"
    assert by_id["plan-only-zh"].outcome.plan_only_zero_execution is True
    assert by_id["repair-then-valid-zh"].outcome.schema_repaired is True
    assert (
        by_id["unknown-call-outcome-en"].outcome.duplicate_side_effect_observed
        is False
    )
