"""Bounded startup recovery page contract (control plane only)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

RecoveryScanConsumer = Literal["planning", "execution"]
RECOVERY_SCAN_STATES = {
    "planning": ("CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "PLAN_VALIDATED"),
    "execution": ("RUNNING", "RETRYING", "RECOVERING", "OBSERVING", "EVALUATING", "DIAGNOSING", "APPROVED", "EXECUTION_READY"),
}


class AgentRecoveryScanPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    lifecycle_ids: tuple[str, ...] = ()
    has_more: bool = False
