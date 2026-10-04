"""Language-independent system presentation; never carries backend prose."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BackendMessageFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    backend_id: str
    status: Literal["available", "unavailable", "disabled"]


class MessageParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    count: int | None = Field(default=None, ge=0)
    subject_ids: tuple[str, ...] = ()
    resource_name: str | None = Field(default=None, max_length=256)
    license: str | None = Field(default=None, max_length=256)
    checksum: str | None = Field(default=None, max_length=256)
    source: Literal["bids", "project", "dicom"] | None = None
    value: str | float | bool | None = None
    decision_kind: Literal["atlas", "template", "global_signal_regression", "repetition_time", "overwrite", "experimental_backend", "subject_id", "other"] | None = None
    backend_facts: tuple[BackendMessageFact, ...] = ()
    artifact_id: str | None = None
    template_id: str | None = None
    diagnostic_id: str | None = Field(default=None, max_length=128)


class SystemMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,127}$")
    params: MessageParameters = Field(default_factory=MessageParameters)

    @model_validator(mode="after")
    def required_facts(self):
        fields = {
            "approval.scope.summary": ("count",),
            "decision.dicom_conversion.impact": ("count",),
            "resource.name": ("resource_name",),
            "resource.details": ("license", "checksum"),
            "option.tr.label": ("source",),
            "option.tr.description": ("source", "value"),
            "science.confirmed": ("decision_kind", "value"),
            "memory.influence": ("decision_kind",),
        }.get(self.code, ())
        if any(getattr(self.params, field) is None for field in fields):
            raise ValueError("SYSTEM_MESSAGE_FACT_REQUIRED")
        return self


def message(code: str, **params) -> SystemMessage:
    return SystemMessage(code=code, params=MessageParameters(**params))
