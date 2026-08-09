from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.capability_enforcement import (
    enforce_node_capabilities,
    enforce_recovery_pipeline_scope,
    filter_recovery_subjects,
)
from src.backend.app.runtime.execution_gateway import (
    ExecutionGateway,
    current_allowlist_hash,
)
from src.backend.app.runtime.node_registry import NODE_REGISTRY
from src.backend.app.runtime.pipeline_executor import run_pipeline
from src.backend.app.runtime.tool_execution_context import ToolExecutionContext
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.pipeline_schema import PipelineNode
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _service(tmp_path: Path) -> ExecutionTicketService:
    return ExecutionTicketService(SQLiteDesktopStore(tmp_path / "capability.sqlite"))


def _issue(
    tmp_path: Path,
    *,
    approved_nodes=("data_inspection",),
    approved_backends=("python",),
):
    service = _service(tmp_path)
    config = tmp_path / "project.yaml"
    pipeline = tmp_path / "pipeline.yaml"
    config.write_text("runtime: {}\n", encoding="utf-8")
    pipeline.write_text("nodes: []\n", encoding="utf-8")
    project = tmp_path / "project"
    rawdata = project / "rawdata"
    inputs = project / "inputs"
    outputs = project / "outputs"
    for path in (rawdata, inputs, outputs):
        path.mkdir(parents=True, exist_ok=True)
    ticket = service.issue(
        project_id="project-1",
        reviewed_plan_id="reviewed-1",
        plan_hash="hash-1",
        goal_contract_hash="goal-contract-hash",
        evaluation_policy_version="goal-evaluator-v1",
        approval_summary_hash="approval-1",
        memory_context_hash=None,
        approved_actor="reviewer",
        approved_node_ids=approved_nodes,
        approved_backend_ids=approved_backends,
        input_roots=[str(project), str(inputs), str(rawdata)],
        output_roots=[str(project), str(outputs)],
        readonly_roots=[str(rawdata)],
        project_config_path=str(config),
        pipeline_path=str(pipeline),
        allowlist_hash=current_allowlist_hash(),
        normalized_params_hash="normalized-params-hash",
        contract_versions=dict.fromkeys(approved_nodes, "1.0.0"),
        audit_id="audit-1",
    )
    return service, ticket, project, rawdata, inputs, outputs


def _context(service, ticket):
    return ToolExecutionContext.from_ticket(ticket, service)


def _node(**params) -> PipelineNode:
    return PipelineNode(
        id=str(params.pop("id", "data_inspection")),
        name="test",
        agent="test",
        backend=str(params.pop("backend", "python")),
        params=params,
    )


def test_normal_read_and_write_paths_are_allowed(tmp_path):
    service, ticket, _, _, inputs, outputs = _issue(tmp_path)
    source = inputs / "bold.nii"
    source.write_bytes(b"test")
    enforce_node_capabilities(
        _context(service, ticket),
        _node(input_bold=str(source), output_path=str(outputs / "result.nii")),
    )


@pytest.mark.parametrize("kind", ["traversal", "cross_project"])
def test_write_escape_is_rejected_and_audited(tmp_path, kind):
    service, ticket, project, _, _, outputs = _issue(tmp_path)
    target = (
        outputs / ".." / ".." / "escape.nii"
        if kind == "traversal"
        else tmp_path / "other-project" / "result.nii"
    )
    with pytest.raises(SafetyError, match="CAPABILITY_WRITE_PATH_OUTSIDE_ROOT"):
        enforce_node_capabilities(
            _context(service, ticket),
            _node(output_path=str(target)),
        )
    events = service.store.list_execution_ticket_events(ticket.execution_ticket_id)
    assert events[-1].reason == "CAPABILITY_WRITE_PATH_OUTSIDE_ROOT"
    assert not (project / "escape.nii").exists()


def test_rawdata_write_is_rejected(tmp_path):
    service, ticket, _, rawdata, _, _ = _issue(tmp_path)
    with pytest.raises(SafetyError, match="CAPABILITY_RAWDATA_WRITE_FORBIDDEN"):
        enforce_node_capabilities(
            _context(service, ticket),
            _node(output_path=str(rawdata / "modified.nii")),
        )


def test_expired_ticket_and_unapproved_backend_are_rejected(tmp_path):
    service, ticket, *_ = _issue(tmp_path)
    expired = ticket.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    with pytest.raises(SafetyError, match="EXECUTION_TICKET_EXPIRED"):
        enforce_node_capabilities(_context(service, expired), _node())

    with pytest.raises(SafetyError, match="CAPABILITY_BACKEND_NOT_APPROVED"):
        enforce_node_capabilities(
            _context(service, ticket),
            _node(backend="matlab-spm"),
        )


def test_symlink_escape_is_rejected(tmp_path):
    service, ticket, _, _, inputs, _ = _issue(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = inputs / "external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    with pytest.raises(SafetyError, match="CAPABILITY_READ_PATH_OUTSIDE_ROOT"):
        enforce_node_capabilities(
            _context(service, ticket),
            _node(input_path=str(link / "bold.nii")),
        )


def test_unapproved_node_is_rejected_before_runner_call(tmp_path, monkeypatch):
    service, ticket, project, *_ = _issue(
        tmp_path,
        approved_nodes=("another_node",),
    )
    work = project / "work"
    logs = project / "logs"
    config = Path(ticket.project_config_path)
    config.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "test", "root_dir": str(project)},
                "runtime": {
                    "work_dir": str(work),
                    "log_dir": str(logs),
                    "derivatives_dir": str(project / "derivatives"),
                    "report_dir": str(project / "reports"),
                    "matlab_command": "matlab",
                },
                "third_party": {
                    "spm_dir": str(project / "spm"),
                    "dpabi_dir": str(project / "dpabi"),
                },
                "safety": {"rawdata_readonly": True},
            }
        ),
        encoding="utf-8",
    )
    pipeline = Path(ticket.pipeline_path)
    pipeline.write_text(
        yaml.safe_dump(
            {
                "pipeline_id": "capability-test",
                "version": "1",
                "execution": {"run_id": "run-capability"},
                "nodes": [{"id": "data_inspection", "backend": "python", "params": {}}],
            }
        ),
        encoding="utf-8",
    )
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setitem(NODE_REGISTRY, "data_inspection", runner)
    with pytest.raises(SafetyError, match="CAPABILITY_NODE_NOT_APPROVED"):
        ExecutionGateway(service).dispatch(
            execution_ticket_id=ticket.execution_ticket_id,
            project_id=ticket.project_id,
            reviewed_plan_id=ticket.reviewed_plan_id,
            plan_hash=ticket.plan_hash,
            approval_summary_hash=ticket.approval_summary_hash,
            memory_context_hash=ticket.memory_context_hash,
            scope_hash=ticket.scope_hash,
            normalized_params_hash=ticket.normalized_params_hash,
            contract_versions=ticket.contract_versions,
            project_config_path=ticket.project_config_path,
            pipeline_path=ticket.pipeline_path,
            command_id="capability-dispatch",
            run_id="run-1",
            executor=run_pipeline,
        )
    assert called is False


def _recovery_ticket(ticket, *, action="RETRY_FAILED_SUBJECTS"):
    payload = ticket.model_dump(mode="python")
    payload.update(
        ticket_kind="recovery_child",
        parent_execution_ticket_id="parent-ticket",
        parent_ticket_hash="parent-hash",
        parent_run_id="parent-run",
        recovery_approval_id="recovery-approval",
        recovery_proposal_id="recovery-proposal",
        recovery_proposal_hash="proposal-hash",
        recovery_candidate_id="candidate",
        recovery_candidate_hash="candidate-hash",
        recovery_attempt_id="attempt",
        quota_reservation_id="reservation",
        recovery_action=action,
        recovery_node_ids=("data_inspection",),
        recovery_subject_ids=("sub-02",) if action == "RETRY_FAILED_SUBJECTS" else (),
        checkpoint_id="checkpoint-1" if action == "RESUME" else None,
        recovery_run_id="recovery-run",
        output_namespace="recovery_attempts/attempt",
    )
    return ExecutionTicket(**payload)


def test_failed_subject_scope_excludes_successful_subjects_before_runner(tmp_path):
    _, ticket, *_ = _issue(tmp_path)
    child = _recovery_ticket(ticket)
    subjects = [
        {"subject_id": "sub-01", "status": "COMPLETE"},
        {"subject_id": "sub-02", "status": "COMPLETE"},
    ]
    assert filter_recovery_subjects(child, subjects) == [subjects[1]]
    with pytest.raises(SafetyError, match="RECOVERY_SUBJECT_SCOPE_MISMATCH"):
        filter_recovery_subjects(child, [subjects[0]])


def test_resume_scope_requires_exact_remaining_subgraph_and_run_binding(tmp_path):
    _, ticket, *_ = _issue(tmp_path)
    child = _recovery_ticket(ticket, action="RESUME")
    enforce_recovery_pipeline_scope(
        child,
        pipeline_node_ids=("data_inspection",),
        run_id="recovery-run",
    )
    with pytest.raises(SafetyError, match="RECOVERY_PIPELINE_NODE_SCOPE_MISMATCH"):
        enforce_recovery_pipeline_scope(
            child,
            pipeline_node_ids=("data_inspection", "contract_smoke"),
            run_id="recovery-run",
        )
    with pytest.raises(SafetyError, match="RECOVERY_RUN_ID_MISMATCH"):
        enforce_recovery_pipeline_scope(
            child,
            pipeline_node_ids=("data_inspection",),
            run_id="parent-run",
        )
