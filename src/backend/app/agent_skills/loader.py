"""Select safe static Skill text for an already-redacted Harness context."""

from __future__ import annotations

from dataclasses import dataclass

from src.backend.app.agent_skills.registry import (
    BUILTIN_SKILL_IDS,
    AgentSkillRegistry,
    AgentSkillUnavailableError,
)
from src.backend.app.agent_skills.schemas import SkillContextRef


@dataclass(frozen=True)
class AgentSkillLoadResult:
    references: tuple[SkillContextRef, ...]
    markdown: str
    error_codes: tuple[str, ...] = ()


class AgentSkillLoader:
    """Read only static allowlisted resources; never expands model permissions."""

    def __init__(self, registry: AgentSkillRegistry | None = None) -> None:
        self.registry = registry or AgentSkillRegistry()

    def load_for_state(self, *, state: str, context) -> AgentSkillLoadResult:
        """Choose procedures applicable to the current lifecycle state.

        Selection is intentionally state-based because prompt construction
        precedes the untrusted model's ActionEnvelope. Capability validation
        remains authoritative after the model proposes an action.
        """
        available_sections = set(type(context.sections).model_fields)
        references: list[SkillContextRef] = []
        markdown: list[str] = []
        errors: list[str] = []
        for skill_id in BUILTIN_SKILL_IDS:
            try:
                skill = self.registry.load(skill_id)
                if state not in skill.manifest.allowed_states:
                    continue
                if not set(skill.manifest.required_context_sections) <= available_sections:
                    raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
                references.append(skill.reference)
                markdown.append(skill.markdown)
            except AgentSkillUnavailableError:
                errors.append(AgentSkillUnavailableError.code)
        return AgentSkillLoadResult(
            references=tuple(sorted(references, key=lambda ref: ref.skill_id)),
            markdown="\n\n".join(markdown),
            error_codes=tuple(sorted(set(errors))),
        )

    def render(self, refs: tuple[SkillContextRef, ...]) -> AgentSkillLoadResult:
        """Re-read packaged content by persisted hashes for model prompt injection."""
        markdown: list[str] = []
        errors: list[str] = []
        accepted: list[SkillContextRef] = []
        for ref in refs:
            try:
                skill = self.registry.load(ref.skill_id)
                if skill.reference != ref:
                    raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
                markdown.append(skill.markdown)
                accepted.append(ref)
            except AgentSkillUnavailableError:
                errors.append(AgentSkillUnavailableError.code)
        return AgentSkillLoadResult(
            references=tuple(accepted),
            markdown="\n\n".join(markdown),
            error_codes=tuple(sorted(set(errors))),
        )
