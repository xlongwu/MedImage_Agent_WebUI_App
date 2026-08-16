"""Runner-facing capability context derived from a verified execution ticket."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.sandbox import SandboxPolicy
from src.backend.app.services.execution_ticket_service import ExecutionTicketService


@dataclass(frozen=True)
class ToolExecutionContext:
    project_id: str
    reviewed_plan_id: str
    execution_ticket_id: str
    audit_id: str
    approved_node_ids: frozenset[str]
    approved_backend_ids: frozenset[str]
    input_roots: tuple[Path, ...]
    output_roots: tuple[Path, ...]
    readonly_roots: tuple[Path, ...]
    allowlist_hash: str
    normalized_params_hash: str
    contract_versions: dict[str, str]
    sandbox_policies: dict[str, SandboxPolicy]
    expires_at: datetime
    ticket: ExecutionTicket
    ticket_service: ExecutionTicketService

    @classmethod
    def from_ticket(
        cls,
        ticket: ExecutionTicket,
        ticket_service: ExecutionTicketService,
    ) -> ToolExecutionContext:
        return cls(
            project_id=ticket.project_id,
            reviewed_plan_id=ticket.reviewed_plan_id,
            execution_ticket_id=ticket.execution_ticket_id,
            audit_id=ticket.audit_id,
            approved_node_ids=frozenset(ticket.approved_node_ids),
            approved_backend_ids=frozenset(ticket.approved_backend_ids),
            input_roots=tuple(Path(value).resolve() for value in ticket.input_roots),
            output_roots=tuple(Path(value).resolve() for value in ticket.output_roots),
            readonly_roots=tuple(Path(value).resolve() for value in ticket.readonly_roots),
            allowlist_hash=ticket.allowlist_hash,
            normalized_params_hash=ticket.normalized_params_hash,
            contract_versions=dict(ticket.contract_versions),
            sandbox_policies={
                str(item["node_id"]): SandboxPolicy(**item)
                for item in ticket.sandbox_policies
            },
            expires_at=ticket.expires_at,
            ticket=ticket,
            ticket_service=ticket_service,
        )
