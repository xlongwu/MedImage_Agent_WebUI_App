from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxAttemptRecord
from src.backend.app.services.sandbox_output_verifier import SandboxOutputVerifier


def _attempt() -> SandboxAttemptRecord:
    return SandboxAttemptRecord(sandbox_id="sandbox", project_id="project", run_id="run", node_id="node", attempt_id="attempt", execution_ticket_id="ticket", dispatch_id="dispatch", policy_hash="policy", status="RUNNING")


def test_verifier_promotes_only_reloadable_output(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    (root / "output").mkdir(parents=True)
    (root / "output" / "result.json").write_text('{"ok": true}', encoding="utf-8")
    destination = tmp_path / "derivatives"
    manifest = SandboxOutputVerifier().verify_and_promote(attempt=_attempt(), sandbox_root=root, approved_output_roots=(str(destination),))
    assert manifest["files"][0]["relative_path"] == "result.json"
    assert (destination / "run" / "node" / "attempt" / "result.json").is_file()


def test_verifier_rejects_invalid_json_without_promoting(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"
    (root / "output").mkdir(parents=True)
    (root / "output" / "result.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(Exception):
        SandboxOutputVerifier().verify_and_promote(attempt=_attempt(), sandbox_root=root, approved_output_roots=(str(tmp_path / "derivatives"),))
