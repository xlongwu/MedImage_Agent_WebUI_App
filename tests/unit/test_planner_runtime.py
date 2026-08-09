from __future__ import annotations

from src.backend.app.planner.pipeline_planner import (
    draft_pipeline_plan,
    execute_pipeline_plan,
    get_planner_history,
    validate_pipeline_plan,
)


def test_planner_drafts_task_specific_pipeline():
    draft = draft_pipeline_plan(
        {
            "disease_type": "Alzheimer",
            "downstream_task": "ALFF/fALFF analysis",
            "available_data": ["T1w", "BOLD"],
            "constraints": [],
        }
    )

    assert draft["ok"] is True
    assert draft["advice_only"] is True
    assert draft["will_execute_pipeline"] is False
    assert draft["recommended_pipeline_path"].endswith("pipeline_rsfmri_alff_falff.yaml")
    assert draft["candidate_nodes"]


def test_planner_validate_rejects_unknown_pipeline():
    result = validate_pipeline_plan(
        {
            "draft": {
                "plan_id": "bad",
                "recommended_pipeline_path": "examples/not_a_pipeline.yaml",
            }
        }
    )

    assert result["ok"] is False
    assert result["errors"]


def test_planner_history_returns_drafts():
    draft_pipeline_plan(
        {
            "downstream_task": "functional connectivity",
        }
    )
    history = get_planner_history(limit=5)

    assert history["ok"] is True
    assert history["drafts"]


def test_template_planner_rejects_explicit_path_traversal():
    draft = draft_pipeline_plan(
        {"downstream_task": "ALFF", "pipeline_path": "../unsafe.yaml"}
    )

    assert draft["ok"] is False
    assert "pipeline path" in " ".join(draft["errors"]).lower()


def test_planner_execution_requires_approval_for_external_pipeline():
    result = execute_pipeline_plan(
        {
            "draft": {
                "plan_id": "external-approval-test",
                "recommended_pipeline_path": "examples/pipeline_mvp.yaml",
                "request": {"project_config_path": "examples/project_config_dataset.yaml"},
            },
            "approved": False,
        }
    )

    assert result["ok"] is False
    assert result["status"] == "EXECUTION_CONTRACT_REQUIRED"
    assert result["replacement"] == "/api/plans/execute-reviewed"
