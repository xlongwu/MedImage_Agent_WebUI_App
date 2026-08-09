from __future__ import annotations

from src.backend.app.planner.llm_planner import generate_plan_from_goal
from src.backend.app.planner.llm_provider import parse_llm_plan_json
from src.backend.app.planner.reviewed_plan_store import reviewed_plan_identity


def test_rule_planner_uses_canonical_plan_and_redacted_provenance() -> None:
    first = generate_plan_from_goal("compute alff analysis", provider="rule_based")
    second = generate_plan_from_goal("compute alff analysis", provider="rule_based")

    assert first.ok is True
    assert first.planner_invocation is not None
    assert first.planner_evidence is not None
    assert first.planner_invocation.provider_id == "rule_based"
    assert first.planner_invocation.input_hash == second.planner_invocation.input_hash
    assert first.planner_evidence.output_hash == second.planner_evidence.output_hash
    assert first.planner_evidence.invocation_id == first.planner_invocation.invocation_id
    assert first.planner_evidence.fallback_used is False
    serialized = str(first.to_dict())
    assert "prompt_template_hash" in serialized
    assert "api_key" not in serialized.casefold()

    first_id, first_hash = reviewed_plan_identity(
        "project-1",
        first.plan,
        planner_invocation=first.planner_invocation,
        planner_evidence=first.planner_evidence,
    )
    second_id, second_hash = reviewed_plan_identity(
        "project-1",
        second.plan,
        planner_invocation=second.planner_invocation,
        planner_evidence=second.planner_evidence,
    )
    plain_id, plain_hash = reviewed_plan_identity("project-1", first.plan)
    assert (first_id, first_hash) == (second_id, second_hash)
    assert (first_id, first_hash) != (plain_id, plain_hash)


def test_provider_failure_has_structured_evidence_and_no_output_hash(monkeypatch) -> None:
    monkeypatch.delenv("MEDIMAGE_LLM_API_KEY", raising=False)

    result = generate_plan_from_goal("motion", provider="openai_compatible")

    assert result.ok is False
    assert result.plan == {}
    assert result.planner_evidence is not None
    assert result.planner_evidence.failure_code == "LLM_API_KEY_MISSING"
    assert result.planner_evidence.output_hash is None


def test_remote_json_parser_uses_the_same_canonical_plan_schema() -> None:
    parsed = parse_llm_plan_json(
        '{"pipeline_id":"p","nodes":[{"id":"data_inspection","backend":"python",'
        '"depends_on":[],"params":{}}]}'
    )

    assert parsed["nodes"][0]["id"] == "data_inspection"
