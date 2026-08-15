from __future__ import annotations

import shutil

import pytest

from src.backend.app.agent_skills.loader import AgentSkillLoader
from src.backend.app.agent_skills.registry import (
    BUILTIN_SKILL_IDS,
    AgentSkillRegistry,
    AgentSkillUnavailableError,
)
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import (
    HarnessContextBuilder,
    HarnessContextSources,
)


def _context():
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="lifecycle-1", project_id="project-1", state="CREATED", goal_text="Create plan"
    )
    project = ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0,
        total_size="0", current_model_id="none",
    )
    return HarnessContextBuilder().build(sources=HarnessContextSources(lifecycle=lifecycle, project=project))


def test_builtin_manifests_are_static_allowlisted_and_hash_valid() -> None:
    registry = AgentSkillRegistry()

    assert registry.validate_all() == ()
    assert set(BUILTIN_SKILL_IDS) == {"planning_evidence_review.v1"}
    for skill_id in BUILTIN_SKILL_IDS:
        skill = registry.load(skill_id)
        assert skill.reference.skill_id == skill_id
        assert skill.manifest.output_schema_ref == "ActionEnvelope"
        assert skill.markdown


def test_registry_rejects_unknown_ids_and_never_discovers_extra_directories(tmp_path) -> None:
    source_root = AgentSkillRegistry().package_root
    copied_root = tmp_path / "agent_skills"
    shutil.copytree(source_root, copied_root)
    (copied_root / "untrusted.v1").mkdir()
    (copied_root / "untrusted.v1" / "manifest.json").write_text("{}", encoding="utf-8")

    registry = AgentSkillRegistry(package_root=copied_root)
    assert registry.validate_all() == ()
    with pytest.raises(AgentSkillUnavailableError, match="AGENT_SKILL_UNAVAILABLE"):
        registry.load("untrusted.v1")


def test_missing_or_tampered_skill_falls_back_to_base_prompt_with_structured_error(tmp_path) -> None:
    source_root = AgentSkillRegistry().package_root
    copied_root = tmp_path / "agent_skills"
    shutil.copytree(source_root, copied_root)
    (copied_root / "planning_evidence_review.v1" / "SKILL.md").unlink()

    result = AgentSkillLoader(AgentSkillRegistry(package_root=copied_root)).load_for_state(
        state="CREATED", context=_context()
    )

    assert result.references == ()
    assert result.markdown == ""
    assert result.error_codes == ("AGENT_SKILL_UNAVAILABLE",)


def test_skill_loader_exposes_only_manifest_sections_for_current_state() -> None:
    result = AgentSkillLoader().load_for_state(state="CREATED", context=_context())

    assert len(result.references) == 1
    assert result.references[0].skill_id == "planning_evidence_review.v1"
    assert set(result.references[0].sections) == {
        "goal", "policy", "project_evidence", "decision_state", "plan_state", "last_action_result", "budget"
    }
    assert "Do not infer missing data" in result.markdown
