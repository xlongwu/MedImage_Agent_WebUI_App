from __future__ import annotations

import sqlite3

import pytest

from src.backend.app.schemas.sandbox import SandboxAttemptRecord
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _attempt(**updates: object) -> SandboxAttemptRecord:
    payload: dict[str, object] = {
        "sandbox_id": "sandbox-1",
        "project_id": "project-1",
        "run_id": "run-1",
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "execution_ticket_id": "ticket-1",
        "dispatch_id": "dispatch-1",
        "policy_hash": "policy-1",
        "status": "PREPARING",
    }
    payload.update(updates)
    return SandboxAttemptRecord(**payload)


def test_sandbox_attempts_persist_with_project_run_isolation(tmp_path) -> None:
    database_path = tmp_path / "desktop.sqlite"
    store = SQLiteDesktopStore(database_path)
    original = store.add_sandbox_attempt(_attempt())
    assert store.add_sandbox_attempt(_attempt()) == original

    updated = store.update_sandbox_attempt("sandbox-1", status="SUCCEEDED", output_count=2)
    assert updated is not None

    reopened = SQLiteDesktopStore(database_path)
    assert reopened.list_sandbox_attempts_for_run("project-1", "run-1") == [updated]
    assert reopened.list_sandbox_attempts_for_run("other-project", "run-1") == []
    assert reopened.list_sandbox_attempts_for_run("project-1", "other-run") == []
    assert reopened.list_incomplete_sandbox_attempts() == []


def test_sandbox_attempt_id_cannot_alias_another_project(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "desktop.sqlite")
    store.add_sandbox_attempt(_attempt())

    with pytest.raises(sqlite3.IntegrityError):
        store.add_sandbox_attempt(_attempt(project_id="project-2"))
