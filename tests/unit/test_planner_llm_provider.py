from __future__ import annotations

from src.backend.app.planner.pipeline_planner import draft_pipeline_plan


def test_template_planner_uses_explicit_rule_policy():
    draft = draft_pipeline_plan({"downstream_task": "regional homogeneity"})

    assert draft["ok"] is True
    assert draft["planner_mode"] == "rule_based"
    assert draft["recommended_pipeline_path"].endswith("pipeline_rsfmri_reho.yaml")


def test_template_planner_does_not_select_a_remote_provider_from_environment(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "not-used")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected HTTP call")),
    )

    draft = draft_pipeline_plan({"downstream_task": "core preprocessing"})

    assert draft["ok"] is True
    assert draft["planner_mode"] == "rule_based"
