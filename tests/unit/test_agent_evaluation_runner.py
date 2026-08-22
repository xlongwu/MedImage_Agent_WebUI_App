from __future__ import annotations

from inspect import signature

from src.backend.app.services.agent_evaluation_runner import AgentEvaluationRunner


def test_runner_contract_does_not_accept_caller_supplied_outcomes() -> None:
    parameters = signature(AgentEvaluationRunner.run_manifest).parameters

    assert set(parameters) == {"self", "manifest", "model_adapter"}
    assert "outcomes" not in parameters
