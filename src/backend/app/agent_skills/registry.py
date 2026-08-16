"""Static registry and integrity checks for packaged Agent Product Skills."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from src.backend.app.agent_skills.schemas import (
    AgentSkillContextSectionName,
    SkillContextRef,
    SkillManifest,
)
from src.backend.app.runtime.agent_capability_catalog import AGENT_CAPABILITY_CATALOG

BUILTIN_SKILL_IDS: tuple[str, ...] = (
    "planning_evidence_review.v1",
)
_OUTPUT_SCHEMAS = frozenset({"ActionEnvelope"})
_SECTION_NAMES = frozenset(AgentSkillContextSectionName.__args__)


class AgentSkillUnavailableError(ValueError):
    """A safe-to-record resource failure that must not stop the Harness."""

    code = "AGENT_SKILL_UNAVAILABLE"


@dataclass(frozen=True)
class RegisteredSkill:
    manifest: SkillManifest
    markdown: str
    reference: SkillContextRef


def canonical_skill_hash(manifest: SkillManifest | dict[str, object], markdown: str) -> str:
    """Hash canonical manifest metadata (excluding its recursive hash) plus Markdown."""
    raw_manifest = manifest.model_dump(mode="json") if isinstance(manifest, SkillManifest) else dict(manifest)
    raw_manifest.pop("content_hash", None)
    canonical_manifest = json.dumps(
        raw_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical_manifest + b"\n" + markdown.encode("utf-8")).hexdigest()


class AgentSkillRegistry:
    """Resolve only fixed source-registered resources; never discover directories."""

    def __init__(self, *, package_root: Path | None = None) -> None:
        self.package_root = (package_root or Path(__file__).resolve().parent).resolve()

    def load(self, skill_id: str) -> RegisteredSkill:
        if skill_id not in BUILTIN_SKILL_IDS:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        skill_root = (self.package_root / skill_id).resolve()
        if skill_root.parent != self.package_root or not skill_root.is_dir():
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        manifest_path = skill_root / "manifest.json"
        markdown_path = skill_root / "SKILL.md"
        if not manifest_path.is_file() or not markdown_path.is_file():
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        try:
            manifest = SkillManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code) from exc
        self._validate(manifest, skill_id=skill_id, markdown=markdown)
        return RegisteredSkill(
            manifest=manifest,
            markdown=markdown,
            reference=SkillContextRef(
                skill_id=manifest.skill_id,
                version=manifest.version,
                content_hash=manifest.content_hash,
                sections=manifest.required_context_sections,
            ),
        )

    def validate_all(self) -> tuple[AgentSkillUnavailableError, ...]:
        """Check every fixed ID without ever treating an extra directory as a Skill."""
        errors: list[AgentSkillUnavailableError] = []
        seen_versions: set[tuple[str, str]] = set()
        for skill_id in BUILTIN_SKILL_IDS:
            try:
                skill = self.load(skill_id)
                identity = (skill.manifest.skill_id, skill.manifest.version)
                if identity in seen_versions:
                    raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
                seen_versions.add(identity)
            except AgentSkillUnavailableError as exc:
                errors.append(exc)
        return tuple(errors)

    @staticmethod
    def _validate(manifest: SkillManifest, *, skill_id: str, markdown: str) -> None:
        if manifest.skill_id != skill_id or len(markdown.encode("utf-8")) > manifest.max_bytes:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if manifest.output_schema_ref not in _OUTPUT_SCHEMAS:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if len(set(manifest.allowed_actions)) != len(manifest.allowed_actions):
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if len(set(manifest.allowed_states)) != len(manifest.allowed_states):
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if len(set(manifest.required_context_sections)) != len(manifest.required_context_sections):
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if not set(manifest.required_context_sections) <= _SECTION_NAMES:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        for action in manifest.allowed_actions:
            capability = AGENT_CAPABILITY_CATALOG.get(action)
            if capability is None or not set(manifest.allowed_states) <= capability.allowed_states:
                raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
        if canonical_skill_hash(manifest, markdown) != manifest.content_hash:
            raise AgentSkillUnavailableError(AgentSkillUnavailableError.code)
