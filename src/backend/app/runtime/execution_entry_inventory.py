"""Versioned classification of public surfaces that may request execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ExecutionEntryDisposition = Literal["gateway", "proposal/dry-run"]


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
    ExecutionEntry("conversion.prepare", "/api/projects/{project_id}/dicom-conversion/prepare", "conversion_routes", "proposal/dry-run", "Preparation and approval evidence only; no process dispatch."),
    ExecutionEntry("preprocessing.preview", "/api/projects/{project_id}/preprocessing/plan/preview", "preprocessing_routes", "proposal/dry-run", "Produces a candidate plan only."),
)


def inventory_as_dicts() -> list[dict[str, str]]:
    return [asdict(entry) for entry in EXECUTION_ENTRY_INVENTORY]
