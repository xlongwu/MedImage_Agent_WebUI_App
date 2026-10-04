"""Fail-closed guard preventing memory from silently choosing scientific parameters."""

from __future__ import annotations

from typing import Any

from src.backend.app.planner.scientific_parameter_registry import (
    classify_parameter,
    get_parameter_rule,
)
from src.backend.app.schemas.memory import MemoryContext


class MemoryInfluenceError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code


_DECISION_PARAMETER = {
    "atlas": "atlas",
    "template": "template",
    "repetition_time": "tr",
    "global_signal_regression": "include_global_signal",
    "experimental_backend": "backend",
    "overwrite": "overwrite_policy",
}


class MemoryInfluenceGuard:
    """Validate memory-matching values against current-task provenance."""

    def validate(
        self,
        *,
        plan: dict[str, Any],
        memory_context: MemoryContext | None,
        science_answers: dict[str, Any] | None = None,
        project_context_values: dict[str, Any] | None = None,
    ) -> None:
        if memory_context is None or not memory_context.decision_suggestions:
            return
        answers = science_answers or {}
        project_values = project_context_values or {}
        suggested: list[tuple[str, str, Any]] = []
        for suggestion in memory_context.decision_suggestions:
            parameter = _DECISION_PARAMETER.get(suggestion.decision_kind)
            if parameter:
                suggested.append((
                    parameter,
                    suggestion.decision_kind,
                    suggestion.typed_value.get("value"),
                ))
        for node in plan.get("nodes", []):
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or "")
            params = node.get("params") if isinstance(node.get("params"), dict) else {}
            for parameter, decision_kind, suggested_value in suggested:
                actual = node.get("backend") if parameter == "backend" else params.get(parameter)
                if actual != suggested_value:
                    continue
                try:
                    rule = (
                        classify_parameter("backend")
                        if parameter == "backend"
                        else get_parameter_rule(node_id, parameter)
                    )
                except KeyError as exc:
                    raise MemoryInfluenceError(
                        "MEMORY_INFLUENCE_UNCLASSIFIED", str(exc)
                    ) from exc
                if rule.impact == "safety":
                    raise MemoryInfluenceError(
                        "MEMORY_SAFETY_PARAMETER_FORBIDDEN",
                        f"{node_id}.{parameter}",
                    )
                confirmed = answers.get(decision_kind) == suggested_value
                authoritative = project_values.get(parameter) == suggested_value
                if rule.impact == "scientific" and not (confirmed or authoritative):
                    raise MemoryInfluenceError(
                        "MEMORY_SCIENTIFIC_CONFIRMATION_REQUIRED",
                        f"{node_id}.{parameter}",
                    )
