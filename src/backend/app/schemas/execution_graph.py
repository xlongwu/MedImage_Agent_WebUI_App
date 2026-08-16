"""Read-only execution-graph API contract.

The structure is derived solely from a validated reviewed plan; runtime files
only project state onto that fixed structure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ExecutionGraphNodeState = Literal[
    "pending", "preflight", "ready", "running", "succeeded", "partial",
    "failed", "blocked", "skipped", "cancelled", "timeout", "reused",
    "invalidated", "unknown",
]


class ExecutionGraphSubjectSummary(BaseModel):
    total: int | None = None
    observed: int = 0
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    blocked: int = 0
    cancelled: int = 0
    timeout: int = 0
    reused: int = 0
    invalidated: int = 0
    unknown: int = 0


class ExecutionGraphNode(BaseModel):
    node_id: str
    label: str
    backend_id: str
    parallel_level: str
    depends_on: tuple[str, ...] = ()
    risk: Literal["normal", "approval", "high", "unknown"] = "normal"
    planned_input_count: int = 0
    planned_output_count: int = 0
    parameter_keys: tuple[str, ...] = ()
    state: ExecutionGraphNodeState = "pending"
    state_source: Literal["plan", "runtime", "summary", "mixed", "unknown"] = "plan"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    subject_summary: ExecutionGraphSubjectSummary | None = None
    warning_count: int = 0
    error_count: int = 0
    actual_output_count: int = 0
    current: bool = False


class ExecutionGraphEdge(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    state: Literal["pending", "active", "completed", "blocked", "unknown"] = "pending"


class ExecutionGraphResponse(BaseModel):
    schema_version: Literal[1] = 1
    project_id: str
    reviewed_plan_id: str
    plan_hash: str
    run_id: str | None = None
    run_state: str | None = None
    run_terminal: bool = False
    graph_status: Literal["available", "partial", "unavailable"] = "available"
    structure_hash: str
    state_hash: str
    generated_at: datetime
    nodes: tuple[ExecutionGraphNode, ...] = Field(default_factory=tuple)
    edges: tuple[ExecutionGraphEdge, ...] = Field(default_factory=tuple)
    current_node_ids: tuple[str, ...] = Field(default_factory=tuple)
    ready_node_ids: tuple[str, ...] = Field(default_factory=tuple)
    terminal_nodes: int = 0
    total_nodes: int = 0
    node_completion_percent: int | None = None
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    errors: tuple[str, ...] = Field(default_factory=tuple)
