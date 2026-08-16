"""Pipeline Execution State Schema — Phase 3 Productization.

Defines run states, node states, terminal/non-terminal sets,
retry/resume/reuse eligibility, transition tables, and pure helper
functions for the productized pipeline executor.

Schema-only module.  No runtime executor is imported or modified.
No file I/O.  No external-tool execution is enabled.

Reference:
  docs/规划与运行时/流水线执行器产品化契约.md
  docs/规划与运行时/运行重试与恢复契约.md
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════════
# 1. Literal type aliases
# ═══════════════════════════════════════════════════════════════════════

RunState = Literal[
    "created",
    "queued",
    "preflight",
    "approval_required",
    "audit_required",
    "ready",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "partial",
    "interrupted",
    "unknown",
]

NodeState = Literal[
    "pending",
    "skipped",
    "preflight",
    "ready",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "reused",
    "invalidated",
    "unknown",
]


class PersistedNodeState(BaseModel):
    """Versioned on-disk node progress record shared by runtime readers/writers.

    It deliberately contains runtime evidence and bounded metadata. Plans,
    approvals, and full runner parameters are not persisted here.
    """

    schema_version: Literal["state-store-v2"]
    run_id: str
    subject: str
    node: str
    status: str
    started_at: str
    ended_at: str | None = None
    updated_at: str
    log_path: str | None = None
    stderr_log: str | None = None
    outputs: list[Any] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    result_json: Any = None
    returncode: int | None = None

# ═══════════════════════════════════════════════════════════════════════
# 2. Run state categorisation sets
# ═══════════════════════════════════════════════════════════════════════

RUN_TERMINAL_STATES: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "partial",
    "interrupted",
})

RUN_NON_TERMINAL_STATES: frozenset[str] = frozenset({
    "created",
    "queued",
    "preflight",
    "approval_required",
    "audit_required",
    "ready",
    "running",
    "unknown",
})

RUN_SUCCESS_STATES: frozenset[str] = frozenset({
    "succeeded",
})

RUN_FAILURE_STATES: frozenset[str] = frozenset({
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "partial",
    "interrupted",
    "unknown",
})

RUN_RETRY_ELIGIBLE_STATES: frozenset[str] = frozenset({
    "failed",
    "blocked",
    "timeout",
    "partial",
    "interrupted",
})

RUN_RESUME_ELIGIBLE_STATES: frozenset[str] = frozenset({
    "failed",
    "timeout",
    "partial",
    "interrupted",
})

# ═══════════════════════════════════════════════════════════════════════
# 3. Node state categorisation sets
# ═══════════════════════════════════════════════════════════════════════

NODE_TERMINAL_STATES: frozenset[str] = frozenset({
    "skipped",
    "succeeded",
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "reused",
    "invalidated",
})

NODE_NON_TERMINAL_STATES: frozenset[str] = frozenset({
    "pending",
    "preflight",
    "ready",
    "running",
    "unknown",
})

NODE_SUCCESS_STATES: frozenset[str] = frozenset({
    "succeeded",
    "reused",
})

NODE_FAILURE_STATES: frozenset[str] = frozenset({
    "failed",
    "blocked",
    "cancelled",
    "timeout",
    "invalidated",
    "skipped",
    "unknown",
})

NODE_RETRY_ELIGIBLE_STATES: frozenset[str] = frozenset({
    "failed",
    "blocked",
    "timeout",
    "invalidated",
})

NODE_REUSE_ELIGIBLE_STATES: frozenset[str] = frozenset({
    "succeeded",
    "reused",
})

# ═══════════════════════════════════════════════════════════════════════
# 4. Transition tables
# ═══════════════════════════════════════════════════════════════════════

RUN_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # --- non-terminal → pre-execution ---
    "created":            frozenset({"queued", "preflight", "blocked"}),
    "queued":             frozenset({"preflight", "running", "cancelled", "blocked"}),
    "preflight":          frozenset({"approval_required", "audit_required", "ready", "blocked", "failed", "cancelled"}),
    "approval_required":  frozenset({"preflight", "blocked", "cancelled"}),
    "audit_required":     frozenset({"preflight", "blocked", "failed", "cancelled"}),
    "ready":              frozenset({"running", "cancelled", "blocked"}),

    # --- active ---
    "running":            frozenset({"succeeded", "failed", "timeout", "partial", "interrupted", "cancelled", "unknown"}),

    # --- terminal (default: no outgoing transitions) ---
    "succeeded":          frozenset(),
    "failed":             frozenset({"queued", "preflight"}),       # retry pathway
    "blocked":            frozenset({"preflight"}),                  # unblock pathway
    "cancelled":          frozenset({"queued"}),                     # re-submit
    "timeout":            frozenset({"queued", "preflight"}),        # retry pathway
    "partial":            frozenset({"queued", "preflight", "failed", "interrupted"}),
    "interrupted":        frozenset({"queued", "preflight"}),
    "unknown":            frozenset({"preflight", "blocked"}),
}

NODE_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    # --- non-terminal ---
    "pending":           frozenset({"skipped", "preflight", "ready", "blocked"}),
    "preflight":         frozenset({"ready", "blocked", "failed"}),
    "ready":             frozenset({"running", "skipped", "blocked"}),

    # --- active ---
    "running":           frozenset({"succeeded", "failed", "timeout", "blocked", "cancelled", "unknown"}),

    # --- terminal (success) ---
    "succeeded":         frozenset({"reused", "invalidated"}),
    "reused":            frozenset({"invalidated"}),

    # --- terminal (failure / blocked) ---
    "failed":            frozenset({"preflight", "ready"}),          # retry pathway
    "blocked":           frozenset({"preflight"}),                    # unblock
    "timeout":           frozenset({"preflight"}),                    # retry
    "cancelled":         frozenset({"pending"}),                      # re-schedule
    "invalidated":       frozenset({"pending", "preflight", "ready"}),
    "skipped":           frozenset(),                                 # intentionally skipped
    "unknown":           frozenset({"preflight", "blocked"}),
}

# ═══════════════════════════════════════════════════════════════════════
# 5. Pure helper functions
# ═══════════════════════════════════════════════════════════════════════

def is_run_terminal(state: str) -> bool:
    """Return True if *state* is a terminal run state."""
    return state in RUN_TERMINAL_STATES


def is_node_terminal(state: str) -> bool:
    """Return True if *state* is a terminal node state."""
    return state in NODE_TERMINAL_STATES


def is_run_retry_eligible(state: str) -> bool:
    """Return True if a run in *state* is eligible for retry."""
    return state in RUN_RETRY_ELIGIBLE_STATES


def is_run_resume_eligible(state: str) -> bool:
    """Return True if a run in *state* is eligible for resume."""
    return state in RUN_RESUME_ELIGIBLE_STATES


def is_node_retry_eligible(state: str) -> bool:
    """Return True if a node in *state* is eligible for retry."""
    return state in NODE_RETRY_ELIGIBLE_STATES


def is_node_reuse_eligible(state: str) -> bool:
    """Return True if a node in *state* may have outputs reused."""
    return state in NODE_REUSE_ELIGIBLE_STATES


def can_transition_run(from_state: str, to_state: str) -> bool:
    """Return True if *from_state* → *to_state* is an allowed run transition.

    Unknown or invalid state strings return False."""
    allowed = RUN_ALLOWED_TRANSITIONS.get(from_state)
    if allowed is None:
        return False
    return to_state in allowed


def can_transition_node(from_state: str, to_state: str) -> bool:
    """Return True if *from_state* → *to_state* is an allowed node transition.

    Unknown or invalid state strings return False."""
    allowed = NODE_ALLOWED_TRANSITIONS.get(from_state)
    if allowed is None:
        return False
    return to_state in allowed


# ═══════════════════════════════════════════════════════════════════════
# 6. Optional Pydantic models for structured I/O
# ═══════════════════════════════════════════════════════════════════════

class RunStateTransition(BaseModel):
    from_state: str
    to_state: str
    allowed: bool
    reason: str | None = None


class NodeStateTransition(BaseModel):
    from_state: str
    to_state: str
    allowed: bool
    reason: str | None = None
