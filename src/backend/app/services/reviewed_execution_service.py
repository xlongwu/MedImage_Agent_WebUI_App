"""Shared entry point for the existing reviewed execution application flow."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class ReviewedExecutionService:
    """Invoke the single reviewed execution path exactly once per command."""

    def __init__(self, executor: Callable[[Any], dict[str, Any]] | None = None) -> None:
        self._executor = executor

    def execute(self, request: Any) -> dict[str, Any]:
        executor = self._executor
        if executor is None:
            # Delayed import prevents a route/service import cycle while this
            # service remains the single application entry point.
            from src.backend.app.api.execute_reviewed_routes import _execute_reviewed_application

            executor = _execute_reviewed_application
        return executor(request)
