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
