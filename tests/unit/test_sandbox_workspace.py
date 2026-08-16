from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxLimits, SandboxPolicy
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.sandbox_workspace_service import SandboxWorkspaceService


def _policy() -> SandboxPolicy:
    return SandboxPolicy(
        node_id="external_node", backend_id="external", provider="windows_restricted_process",
        executable_id="external", executable_path_hash="hash", readonly_root_hashes=(),
        output_root_hashes=(), allowed_environment_keys=("SystemRoot",),
        limits=SandboxLimits(timeout_seconds=10, memory_limit_bytes=16 * 1024 * 1024, max_processes=1),
        policy_hash="policy-hash",
    )


def test_workspace_is_deterministic_and_stages_a_verified_copy(tmp_path: Path) -> None:
    store = SQLiteDesktopStore(tmp_path / "state.sqlite")
    service = SandboxWorkspaceService(store)
    work = tmp_path / "work"
    work.mkdir()
    source = tmp_path / "input.txt"
    source.write_text("approved", encoding="utf-8")
    first = service.prepare(project_id="project", run_id="run", node_id="node", subject_id=None, execution_ticket_id="ticket", dispatch_id="dispatch", policy=_policy(), project_work_root=work, approved_project_root=tmp_path)
    second = service.prepare(project_id="project", run_id="run", node_id="node", subject_id=None, execution_ticket_id="ticket", dispatch_id="dispatch", policy=_policy(), project_work_root=work, approved_project_root=tmp_path)
    assert first.sandbox_id == second.sandbox_id
    manifest = service.stage_inputs(attempt=first, project_work_root=work, inputs=(source,), approved_input_roots=(tmp_path,))
    assert manifest[0]["size_bytes"] == len("approved")
    assert (work / "sandboxes" / "run" / "node" / first.attempt_id / "staged_input" / "input.txt").read_text() == "approved"


@pytest.mark.parametrize("unsafe", ["..", "a/b", "C:\\temp"])
def test_workspace_rejects_unsafe_identifiers(tmp_path: Path, unsafe: str) -> None:
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SafetyError, match="SANDBOX_PATH_OUTSIDE_PROJECT"):
        SandboxWorkspaceService(SQLiteDesktopStore(tmp_path / "state.sqlite")).prepare(project_id="project", run_id=unsafe, node_id="node", subject_id=None, execution_ticket_id="ticket", dispatch_id="dispatch", policy=_policy(), project_work_root=work, approved_project_root=tmp_path)


def test_workspace_rejects_inputs_outside_the_ticket_roots(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("not approved", encoding="utf-8")
    service = SandboxWorkspaceService(SQLiteDesktopStore(tmp_path / "state.sqlite"))
    attempt = service.prepare(project_id="project", run_id="run", node_id="node", subject_id=None, execution_ticket_id="ticket", dispatch_id="dispatch", policy=_policy(), project_work_root=work, approved_project_root=tmp_path)
    with pytest.raises(SafetyError, match="SANDBOX_INPUT_NOT_APPROVED"):
        service.stage_inputs(attempt=attempt, project_work_root=work, inputs=(outside,), approved_input_roots=(work,))
