from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RegisteredScientificResource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    path: str
    license: str
    checksum: str


class ScientificResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1)
    license: str = Field(min_length=1, max_length=120)


class ProjectAgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    default_atlas: RegisteredScientificResource | None = None
    default_template: RegisteredScientificResource | None = None
    cpu_policy: Literal["auto", "serial", "process"] = "auto"
    compute_policy: Literal["auto", "cpu", "gpu"] = "auto"


class UpdateProjectAgentSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_atlas: ScientificResourceInput | None = None
    default_template: ScientificResourceInput | None = None
    cpu_policy: Literal["auto", "serial", "process"] = "auto"
    compute_policy: Literal["auto", "cpu", "gpu"] = "auto"
