from __future__ import annotations

from src.backend.app.schemas.sandbox import SandboxAttemptRecord
from src.backend.app.services.sandbox_attempt_reconciler import SandboxAttemptReconciler
from src.backend.app.services.mock_store import SQLiteDesktopStore


def test_startup_marks_previous_process_attempt_interrupted(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "state.sqlite")
    store.add_sandbox_attempt(SandboxAttemptRecord(
        sandbox_id="sandbox", project_id="project", run_id="run", node_id="node", attempt_id="attempt",
        execution_ticket_id="ticket", dispatch_id="dispatch", policy_hash="policy", status="RUNNING", owner_pid=1,
    ))
    assert SandboxAttemptReconciler(store).reconcile_incomplete_on_startup() == 1
    attempt = store.get_sandbox_attempt("sandbox")
    assert attempt is not None
    assert (attempt.status, attempt.result_code) == ("INTERRUPTED", "SANDBOX_BACKEND_RESTARTED")
