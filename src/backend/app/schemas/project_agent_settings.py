from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.backend.app.schemas.agent_team import Sha256, canonical_hash

AgentTeamConsentDataCategory = Literal[
    "goal_and_contract",
    "deidentified_science_evidence",
    "capability_and_tool_summary",
    "policy_and_scope_summary",
    "decision_and_plan_coverage",
    "environment_summary",
    "budget_summary",
]
REQUIRED_MULTI_AGENT_DATA_CATEGORIES: tuple[AgentTeamConsentDataCategory, ...] = (
    "goal_and_contract",
    "deidentified_science_evidence",
    "capability_and_tool_summary",
    "policy_and_scope_summary",
    "decision_and_plan_coverage",
    "environment_summary",
    "budget_summary",
)


class ProjectMultiAgentConsent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    consent_epoch: int = Field(default=0, ge=0)
    consent_hash: Sha256
    allowed_data_categories: tuple[AgentTeamConsentDataCategory, ...] = ()
    max_model_calls: int = Field(default=6, ge=3, le=6)
    max_input_tokens: int = Field(default=16384, ge=1, le=65536)
    updated_at: datetime | None = None

    @classmethod
    def build(
        cls,
        *,
        project_id: str,
        enabled: bool,
        consent_epoch: int,
        max_model_calls: int = 6,
        max_input_tokens: int = 16384,
        updated_at: datetime | None = None,
    ) -> ProjectMultiAgentConsent:
        categories = REQUIRED_MULTI_AGENT_DATA_CATEGORIES if enabled else ()
        identity = {
            "schema_version": 1,
            "project_id": project_id,
            "enabled": enabled,
            "consent_epoch": consent_epoch,
            "allowed_data_categories": list(categories),
            "max_model_calls": max_model_calls,
            "max_input_tokens": max_input_tokens,
        }
        return cls(
            **identity,
            consent_hash=canonical_hash(identity),
            updated_at=updated_at,
        )

    @model_validator(mode="after")
    def validate_consent_identity(self) -> ProjectMultiAgentConsent:
        expected_categories = REQUIRED_MULTI_AGENT_DATA_CATEGORIES if self.enabled else ()
        if self.allowed_data_categories != expected_categories:
            raise ValueError("PROJECT_MULTI_AGENT_CONSENT_CATEGORIES_INVALID")
        identity = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "enabled": self.enabled,
            "consent_epoch": self.consent_epoch,
            "allowed_data_categories": list(self.allowed_data_categories),
            "max_model_calls": self.max_model_calls,
            "max_input_tokens": self.max_input_tokens,
        }
        if self.consent_hash != canonical_hash(identity):
            raise ValueError("PROJECT_MULTI_AGENT_CONSENT_HASH_MISMATCH")
        return self

    @classmethod
    def default_for_project(cls, project_id: str) -> ProjectMultiAgentConsent:
        return cls.build(
            project_id=project_id,
            enabled=False,
            consent_epoch=0,
            updated_at=None,
        )


class RegisteredScientificResource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str
    license: str
    checksum: str
    space: Literal["MNI152", "native"]


class ScientificResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    license: str = Field(min_length=1, max_length=120)
    space: Literal["MNI152", "native"]


class ProjectAgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    default_atlas: RegisteredScientificResource | None = None
    default_template: RegisteredScientificResource | None = None
    cpu_policy: Literal["auto", "serial", "process"] = "auto"
    compute_policy: Literal["auto", "cpu", "gpu"] = "auto"
    multi_agent_consent: ProjectMultiAgentConsent


class UpdateProjectAgentSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_atlas: ScientificResourceInput | None = None
    default_template: ScientificResourceInput | None = None
    cpu_policy: Literal["auto", "serial", "process"] = "auto"
    compute_policy: Literal["auto", "cpu", "gpu"] = "auto"
    multi_agent_consent_enabled: bool | None = None
    multi_agent_max_model_calls: int | None = Field(default=None, ge=3, le=6)
    multi_agent_max_input_tokens: int | None = Field(default=None, ge=1, le=65536)
