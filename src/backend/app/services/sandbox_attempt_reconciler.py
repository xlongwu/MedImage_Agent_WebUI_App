"""Bounded startup recovery for persisted sandbox attempts."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Protocol

from src.backend.app.schemas.sandbox import SandboxAttemptRecord


class SandboxAttemptReconcileStore(Protocol):
    def list_incomplete_sandbox_attempts(self) -> list[SandboxAttemptRecord]: ...
    def update_sandbox_attempt(self, sandbox_id: str, **updates: object) -> SandboxAttemptRecord | None: ...


class SandboxAttemptReconciler:
    """Never retries a process after restart; unknown work is interrupted."""

    def __init__(self, store: SandboxAttemptReconcileStore) -> None:
        self.store = store

    def reconcile_incomplete_on_startup(self) -> int:
        changed = 0
        for attempt in self.store.list_incomplete_sandbox_attempts():
            if attempt.owner_pid == os.getpid():
                continue
            updated = self.store.update_sandbox_attempt(
                attempt.sandbox_id,
                status="INTERRUPTED",
                result_code="SANDBOX_BACKEND_RESTARTED",
                ended_at=datetime.now(UTC),
            )
            if updated is not None:
                changed += 1
        return changed
