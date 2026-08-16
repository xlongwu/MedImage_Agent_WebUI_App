"""Versioned classification of public surfaces that may request execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ExecutionEntryDisposition = Literal["gateway", "proposal/dry-run", "deprecated"]


@dataclass(frozen=True)
class ExecutionEntry:
    entry_id: str
    route: str
    owner: str
    disposition: ExecutionEntryDisposition
    reason: str


EXECUTION_ENTRY_INVENTORY: tuple[ExecutionEntry, ...] = (
    ExecutionEntry(
        "reviewed.execute",
        "/api/plans/execute-reviewed",
        "execute_reviewed_routes",
        "gateway",
        "The only public pipeline dispatch route; backend issues and consumes a ticket.",
    ),
    ExecutionEntry("retry.execute", "/api/retry/execute", "agent_routes", "deprecated", "Retry must be coordinated by the lifecycle orchestrator."),
    ExecutionEntry("pipeline.task", "/api/pipelines/run", "task_routes", "deprecated", "Non-simulated task execution lacks a reviewed-plan ticket."),
    ExecutionEntry("conversion.execute", "/api/projects/{project_id}/conversion/execute", "conversion_routes", "deprecated", "Retired compatibility route; use native_dicom_conversion_execute inside /api/plans/execute-reviewed."),
    ExecutionEntry("preprocessing.execute", "/api/projects/{project_id}/preprocessing/runs/{run_id}/execute-reviewed", "preprocessing_routes", "deprecated", "The legacy orchestrator is not ticket-bound."),
    ExecutionEntry("realdata.workflow", "/api/workflow/run", "realdata_routes", "deprecated", "Workflow shortcuts cannot bypass reviewed execution."),
    ExecutionEntry("external_smoke.run", "/api/external-smoke/run", "external_smoke_routes", "deprecated", "External processes require gateway capability and environment gates."),
    ExecutionEntry("dpabi.template.execute", "/api/dpabi/template-execute", "dpabi_routes", "deprecated", "Legacy external DPABI execution is retired; use native preprocessing."),
    ExecutionEntry("dpabi.single.execute", "/api/dpabi/run-single-function", "dpabi_routes", "deprecated", "Legacy external DPABI execution is retired; use native preprocessing."),
    ExecutionEntry("dpabi.legacy", "/api/dpabi/*execute*", "dpabi_routes", "deprecated", "Legacy process-launch surfaces fail closed."),
    ExecutionEntry("gpu.legacy", "/api/gpu/*benchmark*", "gpu_routes", "deprecated", "GPU execution cannot rely on route-local flags."),
    ExecutionEntry("preprocessing.legacy", "/api/projects/{project_id}/preprocessing/*execute*", "preprocessing_routes", "deprecated", "Legacy sandbox and native execution is not ticket-bound."),
    ExecutionEntry("dashboard.legacy", "/api/* (deprecated dashboard duplicates)", "dashboard_routes", "deprecated", "Duplicate legacy execution adapters are fail-closed."),
    ExecutionEntry("rsfmri.legacy", "/api/rsfmri/*", "rsfmri_routes", "deprecated", "Legacy pipeline-path routes cannot prove reviewed authority."),
    ExecutionEntry("conversion.prepare", "/api/projects/{project_id}/dicom-conversion/prepare", "conversion_routes", "proposal/dry-run", "Preparation and approval evidence only; no process dispatch."),
    ExecutionEntry("preprocessing.preview", "/api/projects/{project_id}/preprocessing/plan/preview", "preprocessing_routes", "proposal/dry-run", "Produces a candidate plan only."),
)


def inventory_as_dicts() -> list[dict[str, str]]:
    return [asdict(entry) for entry in EXECUTION_ENTRY_INVENTORY]
