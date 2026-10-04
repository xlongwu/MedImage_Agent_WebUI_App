"""Supported intent and input prerequisites remain independent."""
import pytest

from src.backend.app.planner.goal_contract_builder import build_goal_contract_semantics
from src.backend.app.planner.llm_planner import generate_plan_from_goal


@pytest.mark.parametrize("goal", [
    "检查头动并生成 QC 报告，不修改 rawdata。",
    "Check head motion and generate a QC report; rawdata stays read only.",
    "Run motion QC and produce a report",
    "运动校正并生成质控报告",
])
def test_motion_intent_has_consistent_review_contract(goal):
    candidate = generate_plan_from_goal(goal, provider="rule_based")
    assert candidate.ok
    contract = build_goal_contract_semantics(candidate.plan, goal)
    assert contract.ok, contract.reason
    assert contract.semantics["goal_kind"] == "motion_qc"
    assert contract.semantics["minimum_capability_level"] == "metadata_only"
    assert not any(c["criterion_type"].startswith("artifact_") for c in contract.semantics["criteria"])


@pytest.mark.parametrize("goal", [
    "检查头动，仅生成计划，不执行计算，不修改 rawdata",
    "Plan only for head motion QC, without execution",
])
def test_motion_plan_only_never_selects_execution_nodes(goal):
    candidate = generate_plan_from_goal(goal, provider="rule_based")
    assert candidate.ok
    assert candidate.plan["metadata"]["plan_only"] is True
    assert candidate.plan["metadata"]["execution_enabled"] is False
    assert candidate.goal_contract_candidate["minimum_capability_level"] == "metadata_only"


@pytest.mark.parametrize("goal", [
    "根据头动诊断帕金森病", "Predict diagnosis from head motion QC",
    "头动预测", "Explain the motion of planets",
])
def test_nearby_unsupported_motion_goal_is_rejected(goal):
    candidate = generate_plan_from_goal(goal, provider="rule_based")
    assert not candidate.ok
    assert any("UNSUPPORTED_GOAL" in e for e in candidate.errors)


@pytest.mark.parametrize("goal", ["运行静息态预处理", "进行静息态预处理", "Run resting-state preprocessing"])
def test_supported_preprocessing_without_registered_input_requires_input(goal):
    candidate = generate_plan_from_goal(goal, provider="rule_based")
    assert not candidate.ok
    assert any("REGISTERED_FUNCTIONAL_INPUT_REQUIRED" in e for e in candidate.errors)
    assert not any("UNSUPPORTED_GOAL" in e for e in candidate.errors)


def test_untrusted_metadata_cannot_make_arbitrary_nodes_reviewable():
    plan = {"nodes": [{"id": "unregistered_node"}], "metadata": {
        "goal_kind": "motion_qc", "plan_only": True,
        "execution_enabled": False, "capability_level": "metadata_only",
    }}
    assert not build_goal_contract_semantics(plan, "Check head motion").ok


@pytest.mark.parametrize("goal", [
    "检查头动并生成 QC 报告，不修改 rawdata。",
    "Check head motion and generate a QC report",
    "检查头动，仅生成计划，不执行计算，不修改 rawdata",
])
def test_real_agent_task_saves_motion_contract_without_execution(tmp_path, goal):
    from pathlib import Path
    from src.backend.app.schemas.desktop import ProjectDetail
    from src.backend.app.services.mock_store import SQLiteDesktopStore
    from tests.unit.test_agent_task_commands import AgentTaskCommandService

    store = SQLiteDesktopStore(tmp_path / "motion.sqlite")
    store.add_project(ProjectDetail(
        id="motion-project", name="fixture", study_id="fixture", modality="rs-fMRI",
        created_date="fixture", subjects_count=0, current_pipeline_id="", sequences=[],
        scans_count=0, total_size="0", current_model_id="",
        metadata={"project_dir": str(tmp_path), "project_config_path": str(Path("examples/project_config_synthetic_smoke.yaml").resolve())},
    ), health_status="ready", rawdata_dir="")
    task = AgentTaskCommandService(store).create(
        project_id="motion-project", goal=goal, command_id="motion-create", actor="test",
    )
    assert task.state == ("SUCCEEDED" if "仅生成计划" in goal else "WAITING_FOR_APPROVAL")
    plan = store.get_reviewed_plan(task.reviewed_plan_id)
    assert plan is not None
    assert plan.payload["goal_contract"]["minimum_capability_level"] == "metadata_only"
    assert task.run_id is None and task.execution_ticket_id is None
    assert store.list_execution_tickets("motion-project") == []
    assert store.list_run_links("motion-project") == []
