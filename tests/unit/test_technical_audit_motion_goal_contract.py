"""Audit regression for the supported planner-to-GoalContract boundary."""

from src.backend.app.planner.goal_contract_builder import build_goal_contract_semantics
from src.backend.app.planner.llm_planner import generate_plan_from_goal


def test_supported_motion_goal_has_reviewable_goal_contract():
    goal = "检查头动并生成 QC 报告，不修改 rawdata。"
    candidate = generate_plan_from_goal(goal, provider="rule_based")
    assert candidate.ok is True
    assert candidate.plan["pipeline_id"] == "planned_motion_qc"
    # The real Agent Task checks this contract before persisting approval.
    contract = build_goal_contract_semantics(candidate.plan, goal)
    assert contract.ok is True, contract.reason
    assert contract.semantics is not None
