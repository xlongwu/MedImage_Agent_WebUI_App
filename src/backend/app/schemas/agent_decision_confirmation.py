"""Current-task user confirmations, never memory suggestions or authority."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict

from src.backend.app.schemas.agent_lifecycle import DecisionItem


class AgentDecisionConfirmation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    project_id: str
    lifecycle_id: str
    goal_hash: str
    kind: str
    value: str
    evidence_snapshot_hash: str
    batch_id: str
    confirmed_at: datetime
    expires_at: datetime
    status: Literal["confirmed", "revoked"] = "confirmed"


def normalize_decision_value(item: DecisionItem, value: str) -> str:
    if item.answer_type == "number":
        try:
            number = Decimal(value.strip())
            return str(number.normalize()) if number.is_finite() else ""
        except InvalidOperation:
            return ""
    return value.strip()


def value_is_offered(item: DecisionItem, value: str) -> bool:
    if item.readiness != "ready":
        return False
    if item.answer_type == "option":
        return value in {option.id for option in item.options}
    if item.answer_type == "boolean":
        return value in {"true", "false"}
    if item.answer_type == "number":
        try:
            number = Decimal(value)
            return number.is_finite() and (
                item.min_value is None or number >= Decimal(str(item.min_value))
            ) and (item.max_value is None or number <= Decimal(str(item.max_value)))
        except InvalidOperation:
            return False
    return False
