from __future__ import annotations

from pathlib import Path

from src.backend.app.agent_skills.registry import BUILTIN_SKILL_IDS


def test_stable_agent_skill_documentation_matches_the_static_registry() -> None:
    """Stable product documentation may only name statically registered Skills."""

    stable_docs = (
        Path("PROJECT_STATE.md"),
        Path("docs/架构与决策/系统架构.md"),
        Path("docs/规划与运行时/受控单AgentHarness.md"),
    )
    text = "\n".join(path.read_text(encoding="utf-8") for path in stable_docs)

    assert set(BUILTIN_SKILL_IDS) == {"planning_evidence_review.v1"}
    assert "planning_evidence_review.v1" in text
    assert "exactly three versioned Product Skills" not in text
