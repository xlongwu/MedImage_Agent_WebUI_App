from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_evidence import EvidenceSnapshot
from src.backend.app.schemas.agent_execution_wake import AgentExecutionWakeRecord
from src.backend.app.schemas.agent_harness import (
    AgentActionRecord,
    AgentHarnessAttempt,
    AgentHarnessContext,
    AgentHarnessStep,
    AgentPlanningBaseContext,
    AgentTeamRoleContext,
)
from src.backend.app.schemas.agent_invariant import AgentInvariantAuditRecord
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_recovery_scan import (
    RECOVERY_SCAN_STATES,
    AgentRecoveryScanPage,
    RecoveryScanConsumer,
)
from src.backend.app.schemas.agent_task_wake import AgentTaskWakeRecord
from src.backend.app.schemas.agent_team import (
    AgentReviewerKind,
    AgentTeamAdvisory,
    AgentTeamContextSnapshot,
    AgentTeamEvent,
    AgentTeamFinding,
    AgentTeamModelCallRecord,
    AgentTeamRecord,
    AgentTeamWakeRecord,
    AgentTeamWorkItem,
    canonical_hash,
    team_transition_allowed,
    wake_transition_allowed,
    work_item_transition_allowed,
)
from src.backend.app.schemas.desktop import (
    ApprovalRecord,
    DatasetImportRequest,
    DatasetImportResponse,
    DatasetSummary,
    ModelStatus,
    ProjectDetail,
    ProjectSummary,
    ReviewedPlanRecord,
    RunLinkRecord,
    StudyOverview,
    TaskDetail,
    TaskEvent,
    TaskLogEntry,
    TaskStatus,
)
from src.backend.app.schemas.execution_environment import ExecutionEnvironmentSnapshot
from src.backend.app.schemas.execution_ticket import ExecutionTicket, ExecutionTicketEvent
from src.backend.app.schemas.gateway_dispatch import GatewayDispatch, GatewayDispatchEvent
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.observation import ObservationRecord
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.schemas.recovery import DiagnosisRecord, RecoveryProposal
from src.backend.app.schemas.recovery_attempt import (
    RecoveryApprovalEvent,
    RecoveryApprovalRecord,
    RecoveryAttemptEvent,
    RecoveryAttemptRecord,
    RecoveryQuotaReservation,
)
from src.backend.app.schemas.sandbox import SandboxAttemptRecord

DEFAULT_STORE_PATH = Path("outputs/work/desktop/desktop_state.sqlite")


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _decode_agent_lifecycle(payload: str) -> AgentLifecycleRecord:
    data = json.loads(payload)
    if data.get("schema_version") != 7:
        raise SafetyError("AGENT_LIFECYCLE_VERSION_UNSUPPORTED", code="AGENT_LIFECYCLE_VERSION_UNSUPPORTED")
    return AgentLifecycleRecord.model_validate(data)


def get_desktop_store_path() -> Path:
    return Path(os.environ.get("MEDIMAGE_DESKTOP_STORE_PATH", DEFAULT_STORE_PATH))


def should_seed_demo_data() -> bool:
    value = os.environ.get("MEDIMAGE_DESKTOP_SEED_DEMO_DATA")
    if value is None:
        return False
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class SQLiteDesktopStore:
    """SQLite-backed desktop store with optional deterministic demo data.

    The class keeps the old mock-store surface area so existing API routes and
    tests can keep using `mock_store`, while data now survives backend restarts.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else get_desktop_store_path()
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        if should_seed_demo_data():
            self._seed_if_empty()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    project_order INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS dataset_health (
                    project_id TEXT PRIMARY KEY,
                    health_status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS models (
                    project_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id
                    ON task_events(task_id, id);
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_approvals_task_id_created_at
                    ON approvals(task_id, created_at);
                CREATE TABLE IF NOT EXISTS task_artifacts (
                    task_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    dataset_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    dataset_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviewed_plans (
                    reviewed_plan_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewed_plans_project_hash
                    ON reviewed_plans(project_id, plan_hash);
                CREATE INDEX IF NOT EXISTS idx_reviewed_plans_project_updated
                    ON reviewed_plans(project_id, updated_at);
                CREATE TABLE IF NOT EXISTS run_links (
                    run_link_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    reviewed_plan_id TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_run_links_project_updated
                    ON run_links(project_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_run_links_reviewed_plan
                    ON run_links(reviewed_plan_id, updated_at);
                CREATE TABLE IF NOT EXISTS execution_tickets (
                    execution_ticket_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_tickets_project_issued
                    ON execution_tickets(project_id, issued_at);
                CREATE TABLE IF NOT EXISTS execution_environment_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    environment_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_environment_snapshots_project_captured
                    ON execution_environment_snapshots(project_id, captured_at);
                CREATE TABLE IF NOT EXISTS execution_ticket_events (
                    event_id TEXT PRIMARY KEY,
                    execution_ticket_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_ticket_events_ticket_time
                    ON execution_ticket_events(execution_ticket_id, occurred_at);
                CREATE TABLE IF NOT EXISTS gateway_dispatches (
                    dispatch_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL UNIQUE,
                    execution_ticket_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_dispatches_project_created
                    ON gateway_dispatches(project_id, created_at);
                CREATE TABLE IF NOT EXISTS gateway_dispatch_events (
                    event_id TEXT PRIMARY KEY,
                    dispatch_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(dispatch_id, event_type)
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_dispatch_events_dispatch_time
                    ON gateway_dispatch_events(dispatch_id, occurred_at);
                CREATE TABLE IF NOT EXISTS sandbox_attempts (
                    sandbox_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    dispatch_id TEXT NOT NULL,
                    execution_ticket_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sandbox_attempts_project_run
                    ON sandbox_attempts(project_id, run_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_sandbox_attempts_dispatch
                    ON sandbox_attempts(dispatch_id);
                CREATE TABLE IF NOT EXISTS agent_lifecycles (
                    lifecycle_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_lifecycles_project_updated
                    ON agent_lifecycles(project_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_agent_lifecycles_recovery
                    ON agent_lifecycles(state, lifecycle_id);
                CREATE TABLE IF NOT EXISTS agent_recovery_scans (
                    consumer TEXT NOT NULL,
                    project_scope TEXT NOT NULL,
                    schema_version INTEGER NOT NULL CHECK(schema_version = 1),
                    cursor TEXT NOT NULL,
                    upper_bound TEXT NOT NULL,
                    completed INTEGER NOT NULL,
                    PRIMARY KEY(consumer, project_scope)
                );
                CREATE TABLE IF NOT EXISTS agent_lifecycle_events (
                    event_id TEXT PRIMARY KEY,
                    lifecycle_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_lifecycle_events_lifecycle_time
                    ON agent_lifecycle_events(lifecycle_id, occurred_at);
                CREATE TABLE IF NOT EXISTS agent_task_wake_outbox (
                    wake_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    step_key TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(lifecycle_id, step_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_task_wake_ready
                    ON agent_task_wake_outbox(status, available_at, lifecycle_id);
                CREATE TABLE IF NOT EXISTS agent_execution_wake_outbox (
                    wake_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(lifecycle_id, run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_execution_wake_ready
                    ON agent_execution_wake_outbox(status, available_at, lifecycle_id, run_id);
                CREATE TABLE IF NOT EXISTS agent_harness_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    lifecycle_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(lifecycle_id) REFERENCES agent_lifecycles(lifecycle_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_harness_attempts_project_status
                    ON agent_harness_attempts(project_id, status, updated_at);
                CREATE TABLE IF NOT EXISTS agent_harness_contexts (
                    context_hash TEXT PRIMARY KEY,
                    lifecycle_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(lifecycle_id) REFERENCES agent_lifecycles(lifecycle_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_harness_contexts_lifecycle
                    ON agent_harness_contexts(lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_planning_base_contexts (
                    base_context_hash TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    planning_epoch INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(lifecycle_id, planning_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_planning_base_contexts_project_lifecycle
                    ON agent_planning_base_contexts(project_id, lifecycle_id, planning_epoch);
                CREATE TABLE IF NOT EXISTS agent_teams (
                    team_id TEXT PRIMARY KEY,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(lifecycle_id, team_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_teams_project_status
                    ON agent_teams(project_id, status, updated_at);
                CREATE TABLE IF NOT EXISTS agent_team_contexts (
                    snapshot_hash TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    base_context_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    role_contexts_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(team_id, team_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_contexts_project_lifecycle_team
                    ON agent_team_contexts(project_id, lifecycle_id, team_id);
                CREATE TABLE IF NOT EXISTS agent_team_work_items (
                    work_item_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    reviewer_kind TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(team_id, reviewer_kind, attempt_no)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_work_items_status_available
                    ON agent_team_work_items(status, available_at, team_id);
                CREATE TABLE IF NOT EXISTS agent_team_model_calls (
                    model_call_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    work_item_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    UNIQUE(work_item_id, request_hash, attempt_no)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_model_calls_team_status_started
                    ON agent_team_model_calls(team_id, status, started_at);
                CREATE TABLE IF NOT EXISTS agent_team_findings (
                    finding_id TEXT PRIMARY KEY,
                    finding_hash TEXT NOT NULL UNIQUE,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    reviewer_kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_findings_team_role_severity
                    ON agent_team_findings(team_id, reviewer_kind, severity);
                CREATE TABLE IF NOT EXISTS agent_team_advisories (
                    advisory_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    context_snapshot_hash TEXT NOT NULL,
                    advisory_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(team_id, context_snapshot_hash, advisory_hash)
                );
                CREATE TABLE IF NOT EXISTS agent_team_events (
                    event_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    command_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(command_id, event_type),
                    UNIQUE(team_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_events_team_time
                    ON agent_team_events(team_id, occurred_at);
                CREATE TABLE IF NOT EXISTS agent_team_wake_outbox (
                    wake_id TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    team_epoch INTEGER NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    step_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(team_id, step_key)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_team_wake_ready
                    ON agent_team_wake_outbox(status, available_at, team_id);
                CREATE TABLE IF NOT EXISTS agent_evidence_snapshots (
                    snapshot_hash TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_evidence_snapshots_lifecycle
                    ON agent_evidence_snapshots(lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_harness_steps (
                    step_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    step_no INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(attempt_id) REFERENCES agent_harness_attempts(attempt_id),
                    UNIQUE(attempt_id, step_no)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_harness_steps_attempt_no
                    ON agent_harness_steps(attempt_id, step_no);
                CREATE TABLE IF NOT EXISTS agent_harness_actions (
                    action_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    step_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(attempt_id) REFERENCES agent_harness_attempts(attempt_id),
                    FOREIGN KEY(step_id) REFERENCES agent_harness_steps(step_id)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_harness_actions_attempt_created
                    ON agent_harness_actions(attempt_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_invariant_audits (
                    audit_id TEXT PRIMARY KEY,
                    lifecycle_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    report_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_invariant_audits_lifecycle_time
                    ON agent_invariant_audits(lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observation_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    collected_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_observations_hash
                    ON observations(observation_hash);
                CREATE INDEX IF NOT EXISTS idx_observations_project_lifecycle_time
                    ON observations(project_id, lifecycle_id, collected_at);
                CREATE INDEX IF NOT EXISTS idx_observations_project_run_time
                    ON observations(project_id, run_id, collected_at);
                CREATE TABLE IF NOT EXISTS goal_evaluations (
                    goal_evaluation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    goal_contract_id TEXT NOT NULL,
                    goal_evaluation_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_goal_evaluations_hash
                    ON goal_evaluations(goal_evaluation_hash);
                CREATE INDEX IF NOT EXISTS idx_goal_evaluations_project_lifecycle_time
                    ON goal_evaluations(project_id, lifecycle_id, evaluated_at);
                CREATE INDEX IF NOT EXISTS idx_goal_evaluations_observation_time
                    ON goal_evaluations(observation_id, evaluated_at);
                CREATE TABLE IF NOT EXISTS recovery_diagnoses (
                    diagnosis_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    goal_evaluation_id TEXT NOT NULL,
                    diagnosis_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_diagnoses_hash
                    ON recovery_diagnoses(diagnosis_hash);
                CREATE INDEX IF NOT EXISTS idx_recovery_diagnoses_lifecycle_time
                    ON recovery_diagnoses(project_id, lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS recovery_proposals (
                    recovery_proposal_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    diagnosis_id TEXT NOT NULL,
                    recovery_proposal_hash TEXT NOT NULL,
                    recommended_candidate_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_proposals_hash
                    ON recovery_proposals(recovery_proposal_hash);
                CREATE INDEX IF NOT EXISTS idx_recovery_proposals_lifecycle_time
                    ON recovery_proposals(project_id, lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS recovery_approvals (
                    recovery_approval_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    recovery_proposal_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    approved_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_approvals_lifecycle_time
                    ON recovery_approvals(project_id, lifecycle_id, approved_at);
                CREATE TABLE IF NOT EXISTS recovery_approval_events (
                    event_id TEXT PRIMARY KEY,
                    recovery_approval_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_approval_events_approval_time
                    ON recovery_approval_events(recovery_approval_id, occurred_at);
                CREATE TABLE IF NOT EXISTS recovery_attempts (
                    recovery_attempt_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    recovery_proposal_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_attempts_lifecycle_time
                    ON recovery_attempts(project_id, lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS recovery_attempt_events (
                    event_id TEXT PRIMARY KEY,
                    recovery_attempt_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    command_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_attempt_events_attempt_time
                    ON recovery_attempt_events(recovery_attempt_id, occurred_at);
                CREATE TABLE IF NOT EXISTS recovery_quota_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    lifecycle_id TEXT NOT NULL,
                    recovery_attempt_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_quota_lifecycle_time
                    ON recovery_quota_reservations(project_id, lifecycle_id, created_at);
                CREATE TABLE IF NOT EXISTS memory_consent_ledger (
                    command_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    consent_epoch INTEGER NOT NULL,
                    generate_enabled INTEGER NOT NULL,
                    use_enabled INTEGER NOT NULL,
                    outbox_cutoff_sequence INTEGER NOT NULL,
                    principal TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    explicitly_authorized_backfill INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, consent_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_consent_project_epoch
                    ON memory_consent_ledger(project_id, consent_epoch DESC);
                CREATE TABLE IF NOT EXISTS memory_source_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    source_trust_class TEXT NOT NULL,
                    consent_epoch INTEGER NOT NULL,
                    occurred_at TEXT NOT NULL,
                    UNIQUE(project_id, source_type, source_id, source_hash, consent_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_outbox_project_sequence
                    ON memory_source_outbox(project_id, sequence);
                CREATE TABLE IF NOT EXISTS memory_forget_ledger (
                    forget_ledger_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL UNIQUE,
                    project_id TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    semantic_fingerprint TEXT NOT NULL,
                    source_lineage_fingerprints_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    forget_epoch INTEGER NOT NULL,
                    forget_outbox_sequence INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    principal TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, forget_epoch)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_forget_project_key_epoch
                    ON memory_forget_ledger(project_id, canonical_key, forget_epoch DESC);
                """
            )
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT lifecycle_id, payload FROM agent_lifecycles"
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                if payload.get("schema_version") != 6:
                    continue
                payload.update(
                    schema_version=7,
                    planning_mode="single_agent",
                    planning_team_id=None,
                    planning_team_epoch=0,
                )
                conn.execute(
                    "UPDATE agent_lifecycles SET payload=? WHERE lifecycle_id=? AND payload=?",
                    (
                        json.dumps(payload, ensure_ascii=False),
                        row["lifecycle_id"],
                        row["payload"],
                    ),
                )

    def get_memory_consent(self, project_id: str) -> dict[str, object]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_consent_ledger
                WHERE project_id=? ORDER BY consent_epoch DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return {
                "project_id": project_id,
                "generate_enabled": False,
                "use_enabled": False,
                "consent_epoch": 0,
                "outbox_cutoff_sequence": 0,
                "updated_at": None,
            }
        return {
            "project_id": row["project_id"],
            "generate_enabled": bool(row["generate_enabled"]),
            "use_enabled": bool(row["use_enabled"]),
            "consent_epoch": int(row["consent_epoch"]),
            "outbox_cutoff_sequence": int(row["outbox_cutoff_sequence"]),
            "updated_at": row["created_at"],
        }

    def set_memory_consent(
        self,
        *,
        project_id: str,
        command_id: str,
        principal: str,
        generate_enabled: bool,
        use_enabled: bool,
        explicitly_authorized_backfill: bool = False,
    ) -> dict[str, object]:
        payload_hash = stable_hash(
            {
                "project_id": project_id,
                "generate_enabled": generate_enabled,
                "use_enabled": use_enabled,
                "explicitly_authorized_backfill": explicitly_authorized_backfill,
            }
        )
        with self._lock, self._connect() as conn:
            project = conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if project is None:
                raise ValueError(f"PROJECT_NOT_FOUND: {project_id}")
            replay = conn.execute(
                "SELECT * FROM memory_consent_ledger WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if replay is not None:
                if replay["principal"] != principal or replay["payload_hash"] != payload_hash:
                    raise RuntimeError("MEMORY_CONSENT_COMMAND_CONFLICT")
                return {
                    "project_id": replay["project_id"],
                    "generate_enabled": bool(replay["generate_enabled"]),
                    "use_enabled": bool(replay["use_enabled"]),
                    "consent_epoch": int(replay["consent_epoch"]),
                    "outbox_cutoff_sequence": int(replay["outbox_cutoff_sequence"]),
                    "updated_at": replay["created_at"],
                }
            current = conn.execute(
                "SELECT COALESCE(MAX(consent_epoch), 0) AS value FROM memory_consent_ledger WHERE project_id=?",
                (project_id,),
            ).fetchone()
            cutoff = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM memory_source_outbox"
            ).fetchone()
            epoch = int(current["value"]) + 1
            cutoff_sequence = int(cutoff["value"])
            created_at = utc_now_iso()
            conn.execute(
                """
                INSERT INTO memory_consent_ledger
                    (command_id, project_id, consent_epoch, generate_enabled,
                     use_enabled, outbox_cutoff_sequence, principal, payload_hash,
                     explicitly_authorized_backfill, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    project_id,
                    epoch,
                    1 if generate_enabled else 0,
                    1 if use_enabled else 0,
                    cutoff_sequence,
                    principal,
                    payload_hash,
                    1 if explicitly_authorized_backfill else 0,
                    created_at,
                ),
            )
        return {
            "project_id": project_id,
            "generate_enabled": generate_enabled,
            "use_enabled": use_enabled,
            "consent_epoch": epoch,
            "outbox_cutoff_sequence": cutoff_sequence,
            "updated_at": created_at,
        }

    @staticmethod
    def _append_memory_outbox(
        conn: sqlite3.Connection,
        *,
        project_id: str,
        source_type: str,
        source_id: str,
        source_hash: str,
        source_trust_class: str,
        occurred_at: str,
    ) -> int | None:
        consent = conn.execute(
            """
            SELECT consent_epoch, generate_enabled FROM memory_consent_ledger
            WHERE project_id=? ORDER BY consent_epoch DESC LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        if consent is None or not bool(consent["generate_enabled"]):
            return None
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO memory_source_outbox
                (project_id, source_type, source_id, source_hash,
                 source_trust_class, consent_epoch, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                source_type,
                source_id,
                source_hash,
                source_trust_class,
                int(consent["consent_epoch"]),
                occurred_at,
            ),
        )
        if cursor.rowcount == 1:
            return int(cursor.lastrowid)
        row = conn.execute(
            """
            SELECT sequence FROM memory_source_outbox
            WHERE project_id=? AND source_type=? AND source_id=?
              AND source_hash=? AND consent_epoch=?
            """,
            (
                project_id,
                source_type,
                source_id,
                source_hash,
                int(consent["consent_epoch"]),
            ),
        ).fetchone()
        return int(row["sequence"]) if row else None

    def list_memory_outbox(
        self, project_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, object]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_source_outbox
                WHERE project_id=? AND sequence>?
                ORDER BY sequence LIMIT ?
                """,
                (project_id, max(0, after_sequence), max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_memory_outbox_max_sequence(self, project_id: str) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM memory_source_outbox WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return int(row["value"])

    def get_memory_source_projection(
        self, *, project_id: str, source_type: str, source_id: str
    ) -> dict[str, object] | None:
        """Return only typed, allowlisted fields for a committed source record."""

        table_specs = {
            "agent_lifecycle_event": (
                "agent_lifecycle_events",
                "event_id",
                "project_id",
                ("event_id", "lifecycle_id", "from_state", "to_state", "occurred_at", "payload"),
            ),
            "observation": (
                "observations",
                "observation_id",
                "project_id",
                ("observation_id", "lifecycle_id", "run_id", "observation_hash", "collected_at", "payload"),
            ),
            "goal_evaluation": (
                "goal_evaluations",
                "goal_evaluation_id",
                "project_id",
                (
                    "goal_evaluation_id",
                    "lifecycle_id",
                    "observation_id",
                    "goal_evaluation_hash",
                    "status",
                    "evaluated_at",
                    "payload",
                ),
            ),
            "run_summary": (
                "run_links",
                "run_link_id",
                "project_id",
                ("run_link_id", "reviewed_plan_id", "run_id", "updated_at", "payload"),
            ),
        }
        spec = table_specs.get(source_type)
        if spec is None:
            return None
        table, id_column, project_column, columns = spec
        lifecycle_payload: dict[str, object] | None = None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(columns)} FROM {table} WHERE {id_column}=? AND {project_column}=?",
                (source_id, project_id),
            ).fetchone()
            if row is not None and source_type == "agent_lifecycle_event":
                lifecycle_row = conn.execute(
                    "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=?",
                    (row["lifecycle_id"], project_id),
                ).fetchone()
                if lifecycle_row is not None:
                    lifecycle_payload = json.loads(lifecycle_row["payload"])
        if row is None:
            return None
        result = dict(row)
        payload = result.pop("payload", None)
        if payload:
            decoded = json.loads(payload)
            if source_type == "agent_lifecycle_event":
                result["source_hash"] = stable_hash(decoded)
                details = decoded.get("details") or {}
                answer = details.get("answer")
                science_answers = (
                    ((lifecycle_payload or {}).get("command_context") or {}).get(
                        "science_answers"
                    )
                    if lifecycle_payload
                    else {}
                )
                decision_kind = next(
                    (
                        str(key)
                        for key, value in (science_answers or {}).items()
                        if value == answer
                    ),
                    None,
                )
                result["event"] = {
                    "source_command": decoded.get("source_command"),
                    "details": {
                        **details,
                        **({"decision_kind": decision_kind} if decision_kind else {}),
                    },
                    "to_state": decoded.get("to_state"),
                }
            elif source_type == "observation":
                result["source_hash"] = result.get("observation_hash")
                pipeline = decoded.get("pipeline") or {}
                completeness = decoded.get("completeness") or {}
                capability = decoded.get("capability") or {}
                scientific = decoded.get("scientific") or {}
                result["observation"] = {
                    "execution_status": pipeline.get("status"),
                    "errors": pipeline.get("errors") or [],
                    "warnings": pipeline.get("warnings") or [],
                    "completeness": completeness.get("status"),
                    "conflicts": completeness.get("conflicts") or [],
                    "blocking_facts": completeness.get("blocking_facts") or [],
                    "capability_level": capability.get("defensible_level"),
                    "scientific_status": scientific.get("status"),
                    "limitation_flags": scientific.get("limitation_flags") or [],
                }
            elif source_type == "goal_evaluation":
                result["source_hash"] = result.get("goal_evaluation_hash")
                criterion_results = decoded.get("criterion_results") or []
                result["evaluation"] = {
                    "status": decoded.get("status"),
                    "failed_criteria": [
                        item.get("criterion_id")
                        for item in criterion_results
                        if isinstance(item, dict) and item.get("status") == "failed"
                    ],
                    "reason_codes": [
                        item.get("reason_code")
                        for item in criterion_results
                        if isinstance(item, dict) and item.get("reason_code")
                    ],
                    "warnings": decoded.get("warnings") or [],
                }
            else:
                result["source_hash"] = stable_hash(decoded)
                result["run"] = {
                    "status": decoded.get("status"),
                    "warnings": decoded.get("warnings") or [],
                }
        return result

    def append_memory_forget_ledger(
        self,
        *,
        project_id: str,
        command_id: str,
        principal: str,
        canonical_key: str,
        semantic_fingerprint: str,
        source_lineage_fingerprints: list[str],
        content_hash: str,
        forget_outbox_sequence: int,
        generation: int,
    ) -> dict[str, object]:
        command_payload = {
            "project_id": project_id,
            "canonical_key": canonical_key,
            "semantic_fingerprint": semantic_fingerprint,
            "source_lineage_fingerprints": sorted(source_lineage_fingerprints),
            "content_hash": content_hash,
            "forget_outbox_sequence": forget_outbox_sequence,
            "generation": generation,
        }
        payload_hash = stable_hash(command_payload)
        with self._lock, self._connect() as conn:
            replay = conn.execute(
                "SELECT * FROM memory_forget_ledger WHERE command_id=?", (command_id,)
            ).fetchone()
            if replay is not None:
                if replay["principal"] != principal or replay["payload_hash"] != payload_hash:
                    raise RuntimeError("MEMORY_FORGET_COMMAND_CONFLICT")
                return dict(replay)
            current = conn.execute(
                "SELECT COALESCE(MAX(forget_epoch), 0) AS value FROM memory_forget_ledger WHERE project_id=?",
                (project_id,),
            ).fetchone()
            forget_epoch = int(current["value"]) + 1
            record = {
                "forget_ledger_id": f"memory_forget_{uuid4().hex}",
                "command_id": command_id,
                "project_id": project_id,
                "canonical_key": canonical_key,
                "semantic_fingerprint": semantic_fingerprint,
                "source_lineage_fingerprints_json": json.dumps(
                    sorted(source_lineage_fingerprints), ensure_ascii=False
                ),
                "content_hash": content_hash,
                "forget_epoch": forget_epoch,
                "forget_outbox_sequence": forget_outbox_sequence,
                "generation": generation,
                "principal": principal,
                "payload_hash": payload_hash,
                "created_at": utc_now_iso(),
            }
            conn.execute(
                """
                INSERT INTO memory_forget_ledger
                    (forget_ledger_id, command_id, project_id, canonical_key,
                     semantic_fingerprint, source_lineage_fingerprints_json,
                     content_hash, forget_epoch, forget_outbox_sequence, generation,
                     principal, payload_hash, created_at)
                VALUES (:forget_ledger_id, :command_id, :project_id, :canonical_key,
                        :semantic_fingerprint, :source_lineage_fingerprints_json,
                        :content_hash, :forget_epoch, :forget_outbox_sequence, :generation,
                        :principal, :payload_hash, :created_at)
                """,
                record,
            )
            return record

    def list_memory_forget_ledger(self, project_id: str) -> list[dict[str, object]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_forget_ledger WHERE project_id=? ORDER BY forget_epoch",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _seed_if_empty(self) -> None:
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            if count:
                conn.execute(
                    """
                    INSERT INTO store_meta (key, value)
                    VALUES ('seeded_once', '1')
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """
                )
                return
            seeded = conn.execute(
                "SELECT value FROM store_meta WHERE key = 'seeded_once'"
            ).fetchone()
            if seeded:
                return

            projects = [
                ProjectDetail(
                    id="brain-tumor-study",
                    name="Brain Tumor Study",
                    study_id="BTS-2026-0525",
                    modality="MRI / rs-fMRI",
                    sequences=["T1", "T2", "FLAIR", "T1ce"],
                    subjects_count=128,
                    scans_count=1024,
                    total_size="512 GB",
                    created_date="May 25, 2026",
                    current_pipeline_id="brain-tumor-segmentation",
                    current_model_id="unet3d-v2.1",
                ),
                ProjectDetail(
                    id="ad-cohort",
                    name="AD Cohort",
                    study_id="ADC-2026-0417",
                    modality="rs-fMRI",
                    sequences=["T1", "BOLD"],
                    subjects_count=86,
                    scans_count=344,
                    total_size="224 GB",
                    created_date="April 17, 2026",
                    current_pipeline_id="rsfmri-alff-falff",
                    current_model_id="deterministic-qc-v1",
                ),
                ProjectDetail(
                    id="ms-lesion-analysis",
                    name="MS Lesion Analysis",
                    study_id="MSL-2026-0328",
                    modality="MRI",
                    sequences=["T1", "T2", "FLAIR"],
                    subjects_count=54,
                    scans_count=216,
                    total_size="118 GB",
                    created_date="March 28, 2026",
                    current_pipeline_id="lesion-detection",
                    current_model_id="unet-lesion-v1",
                ),
                ProjectDetail(
                    id="stroke-research",
                    name="Stroke Research",
                    study_id="STR-2026-0211",
                    modality="MRI / DWI",
                    sequences=["DWI", "ADC", "FLAIR"],
                    subjects_count=42,
                    scans_count=168,
                    total_size="96 GB",
                    created_date="February 11, 2026",
                    current_pipeline_id="stroke-qc",
                    current_model_id="qc-baseline-v1",
                ),
            ]
            for index, project in enumerate(projects):
                conn.execute(
                    "INSERT INTO projects (id, payload, project_order) VALUES (?, ?, ?)",
                    (project.id, self._dump_model(project), index),
                )

            dataset_health = {
                "brain-tumor-study": "Healthy",
                "ad-cohort": "Review",
                "ms-lesion-analysis": "Healthy",
                "stroke-research": "Healthy",
            }
            conn.executemany(
                "INSERT INTO dataset_health (project_id, health_status) VALUES (?, ?)",
                list(dataset_health.items()),
            )

            models = [
                ModelStatus(
                    project_id="brain-tumor-study",
                    model_name="UNet 3D",
                    version="v2.1",
                    status="Ready",
                    dice_score=0.892,
                    last_trained="May 15, 2026",
                    metrics={"dice": 0.892, "hausdorff95": 4.8, "sensitivity": 0.91},
                ),
                ModelStatus(
                    project_id="ad-cohort",
                    model_name="Deterministic rs-fMRI QC",
                    version="v1.4",
                    status="Ready",
                    dice_score=0.0,
                    last_trained="N/A",
                    metrics={"qc_pass_rate": 0.94, "mean_fd": 0.18},
                ),
                ModelStatus(
                    project_id="ms-lesion-analysis",
                    model_name="UNet Lesion",
                    version="v1.0",
                    status="Ready",
                    dice_score=0.841,
                    last_trained="April 04, 2026",
                    metrics={"dice": 0.841, "precision": 0.87},
                ),
                ModelStatus(
                    project_id="stroke-research",
                    model_name="QC Baseline",
                    version="v1.0",
                    status="Ready",
                    dice_score=0.0,
                    last_trained="N/A",
                    metrics={"qc_pass_rate": 0.9},
                ),
            ]
            for model in models:
                conn.execute(
                    "INSERT INTO models (project_id, payload) VALUES (?, ?)",
                    (model.project_id, self._dump_model(model)),
                )

            for task in self._seed_tasks():
                now = task.updated_at
                conn.execute(
                    "INSERT INTO tasks (id, payload, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (task.id, self._dump_model(task), now, now),
                )
                for log in task.logs:
                    event = TaskEvent(
                        id=0,
                        task_id=task.id,
                        status=task.status,
                        progress=task.progress,
                        message=log,
                        timestamp=now,
                        result_path=task.result_path,
                        source="seed",
                    )
                    conn.execute(
                        "INSERT INTO task_events (task_id, payload, created_at) VALUES (?, ?, ?)",
                        (task.id, self._dump_model(event, exclude={"id"}), now),
                    )
            conn.execute(
                """
                INSERT INTO store_meta (key, value)
                VALUES ('seeded_once', '1')
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """
            )

    def _seed_tasks(self) -> list[TaskDetail]:
        return [
            TaskDetail(
                id="task-001",
                run_name="Run_2026_0525_001",
                pipeline="SPM + DPABI Smoke",
                dataset="Brain Tumor Study",
                status="running",
                progress=64,
                started_at="09:42",
                duration="00:18:24",
                owner="Dr. Alex Morgan",
                logs=["External smoke package generated", "Awaiting approved smoke run"],
                result_path=None,
                execution_mode="external_smoke",
                project_id="brain-tumor-study",
                pipeline_id="external-smoke",
                model_id="deterministic-planner",
                input_sequences=["T1", "BOLD"],
                output_type="diagnostics",
                updated_at=utc_now_iso(),
            ),
            TaskDetail(
                id="task-002",
                run_name="Run_2026_0524_014",
                pipeline="rs-fMRI ALFF/fALFF",
                dataset="AD Cohort",
                status="completed",
                progress=100,
                started_at="Yesterday",
                duration="01:42:11",
                owner="Dr. Alex Morgan",
                logs=["ALFF/fALFF report exported"],
                result_path="outputs/reports/rsfmri/alff_falff_latest.html",
                execution_mode="rsfmri_python",
                project_id="ad-cohort",
                pipeline_id="rsfmri-alff-falff",
                model_id="deterministic-qc-v1",
                input_sequences=["T1", "BOLD"],
                output_type="qc_report",
                updated_at=utc_now_iso(),
            ),
            TaskDetail(
                id="task-003",
                run_name="Run_2026_0523_009",
                pipeline="ReHo QC",
                dataset="Demo BIDS",
                status="completed",
                progress=100,
                started_at="May 23",
                duration="00:55:47",
                owner="Dr. Alex Morgan",
                logs=["ReHo QC passed"],
                result_path="outputs/reports/rsfmri/reho_latest.html",
                execution_mode="rsfmri_python",
                project_id="brain-tumor-study",
                pipeline_id="rsfmri-reho",
                model_id="deterministic-qc-v1",
                input_sequences=["BOLD"],
                output_type="qc_report",
                updated_at=utc_now_iso(),
            ),
            TaskDetail(
                id="task-004",
                run_name="Run_2026_0522_017",
                pipeline="DPABI y_Filter",
                dataset="Sandbox",
                status="failed",
                progress=20,
                started_at="May 22",
                duration="00:07:32",
                owner="Dr. Alex Morgan",
                logs=["Missing expected DPABI result JSON"],
                result_path=None,
                execution_mode="external_smoke",
                project_id="brain-tumor-study",
                pipeline_id="dpabi-y-filter",
                model_id="matlab-runner",
                input_sequences=["BOLD"],
                output_type="diagnostics",
                updated_at=utc_now_iso(),
            ),
        ]

    def list_projects(self) -> list[ProjectSummary]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM projects ORDER BY project_order, id").fetchall()
        return [
            ProjectSummary(**self._load_payload(row["payload"], ProjectDetail).model_dump(exclude={"sequences", "scans_count", "total_size", "current_model_id", "metadata"}))
            for row in rows
        ]

    def get_project(self, project_id: str) -> ProjectDetail | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._load_payload(row["payload"], ProjectDetail) if row else None

    def update_project_metadata(
        self, project_id: str, updates: dict[str, object]
    ) -> ProjectDetail | None:
        """Atomically merge project metadata without changing dataset health."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            project = self._load_payload(row["payload"], ProjectDetail)
            metadata = dict(project.metadata or {})
            metadata.update(updates)
            updated = project.model_copy(update={"metadata": metadata})
            conn.execute(
                "UPDATE projects SET payload = ? WHERE id = ?",
                (self._dump_model(updated), project_id),
            )
        return updated

    def update_project_agent_settings(
        self,
        project_id: str,
        settings: dict[str, object],
        *,
        expected_consent_hash: str,
    ) -> ProjectDetail | None:
        """Persist settings and consent together with an epoch/hash CAS."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            if row is None:
                return None
            project = self._load_payload(row["payload"], ProjectDetail)
            metadata = dict(project.metadata or {})
            defaults = metadata.get("agent_defaults")
            if not isinstance(defaults, dict):
                defaults = {}
            current_payload = defaults.get("multi_agent_consent")
            current_consent = (
                ProjectMultiAgentConsent.model_validate(current_payload)
                if isinstance(current_payload, dict)
                else ProjectMultiAgentConsent.default_for_project(project_id)
            )
            if current_consent.consent_hash != expected_consent_hash:
                raise RuntimeError("PROJECT_AGENT_SETTINGS_CONCURRENT_UPDATE")
            consent_payload = settings.get("multi_agent_consent")
            if not isinstance(consent_payload, dict):
                raise RuntimeError("PROJECT_AGENT_CONSENT_REQUIRED")
            consent = ProjectMultiAgentConsent.model_validate(consent_payload)
            if consent.project_id != project_id:
                raise RuntimeError("PROJECT_AGENT_CONSENT_PROJECT_MISMATCH")
            consent_changed = (
                consent.enabled != current_consent.enabled
                or consent.allowed_data_categories
                != current_consent.allowed_data_categories
                or consent.max_model_calls != current_consent.max_model_calls
                or consent.max_input_tokens != current_consent.max_input_tokens
            )
            expected_epoch = current_consent.consent_epoch + int(consent_changed)
            if consent.consent_epoch != expected_epoch:
                raise RuntimeError("PROJECT_AGENT_CONSENT_EPOCH_INVALID")
            defaults.update(settings)
            metadata["agent_defaults"] = defaults
            updated = project.model_copy(update={"metadata": metadata})
            conn.execute(
                "UPDATE projects SET payload=? WHERE id=?",
                (self._dump_model(updated), project_id),
            )
        return updated

    def add_project(
        self,
        project: ProjectDetail,
        *,
        health_status: str,
        rawdata_dir: str,
        dataset_type: str = "bids",
        overwrite: bool = False,
    ) -> ProjectDetail:
        """Persist a dashboard project and its referenced rawdata atomically."""
        dataset_id = f"created-{project.id}-rawdata"
        created_at = str(project.metadata.get("created_at") or utc_now_iso())
        rawdata_dir = rawdata_dir.strip()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?",
                (project.id,),
            ).fetchone()
            if existing and not overwrite:
                raise ValueError(f"Project already exists: {project.id}")

            if existing:
                conn.execute(
                    "UPDATE projects SET payload = ? WHERE id = ?",
                    (self._dump_model(project), project.id),
                )
            else:
                next_order = conn.execute(
                    "SELECT COALESCE(MAX(project_order), -1) + 1 FROM projects"
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO projects (id, payload, project_order) VALUES (?, ?, ?)",
                    (project.id, self._dump_model(project), next_order),
                )

            conn.execute(
                """
                INSERT INTO dataset_health (project_id, health_status)
                VALUES (?, ?)
                ON CONFLICT(project_id) DO UPDATE SET health_status = excluded.health_status
                """,
                (project.id, health_status),
            )
            if rawdata_dir:
                conn.execute(
                    """
                    INSERT INTO imports (dataset_id, project_id, path, dataset_type, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(dataset_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        path = excluded.path,
                        dataset_type = excluded.dataset_type,
                        created_at = excluded.created_at
                    """,
                    (dataset_id, project.id, rawdata_dir, dataset_type, created_at),
                )
        return project

    def remove_project(self, project_id: str) -> bool:
        """Remove dashboard records for a project without deleting filesystem data."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if not existing:
                return False

            task_ids: list[str] = []
            rows = conn.execute("SELECT id, payload FROM tasks").fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload"])
                except json.JSONDecodeError:
                    payload = {}
                if payload.get("project_id") == project_id:
                    task_ids.append(str(row["id"]))

            for task_id in task_ids:
                conn.execute("DELETE FROM approvals WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM task_events WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM task_artifacts WHERE task_id = ?", (task_id,))
                conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

            conn.execute("DELETE FROM run_links WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM reviewed_plans WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM execution_ticket_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM execution_tickets WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM gateway_dispatch_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM gateway_dispatches WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_harness_steps WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_harness_contexts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_wake_outbox WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_advisories WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_findings WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_model_calls WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_work_items WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_team_contexts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_teams WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_planning_base_contexts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_harness_attempts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_evidence_snapshots WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_lifecycle_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM agent_lifecycles WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM observations WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM goal_evaluations WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_diagnoses WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_proposals WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_approval_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_approvals WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_attempt_events WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_attempts WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM recovery_quota_reservations WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM imports WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM dataset_health WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM models WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return True

    def add_reviewed_plan(self, record: ReviewedPlanRecord) -> ReviewedPlanRecord:
        """Insert or refresh the index entry for a stable project/plan hash."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                """
                SELECT reviewed_plan_id, payload FROM reviewed_plans
                WHERE project_id = ? AND plan_hash = ?
                """,
                (record.project_id, record.plan_hash),
            ).fetchone()
            if existing:
                current = ReviewedPlanRecord(**json.loads(existing["payload"]))
                updated = record.model_copy(
                    update={
                        "reviewed_plan_id": current.reviewed_plan_id,
                        "created_at": current.created_at,
                        "approval_status": current.approval_status,
                        "execution_status": current.execution_status,
                        "last_audit_id": current.last_audit_id,
                        "last_execution_id": current.last_execution_id,
                        "warnings": list(
                            dict.fromkeys([*current.warnings, *record.warnings])
                        ),
                    }
                )
                conn.execute(
                    """
                    UPDATE reviewed_plans
                    SET payload = ?, updated_at = ?
                    WHERE reviewed_plan_id = ?
                    """,
                    (
                        self._dump_model(updated),
                        updated.updated_at,
                        current.reviewed_plan_id,
                    ),
                )
                return updated

            duplicate_id = conn.execute(
                "SELECT 1 FROM reviewed_plans WHERE reviewed_plan_id = ?",
                (record.reviewed_plan_id,),
            ).fetchone()
            if duplicate_id:
                raise ValueError(
                    f"Reviewed plan id already exists: {record.reviewed_plan_id}"
                )
            conn.execute(
                """
                INSERT INTO reviewed_plans
                    (reviewed_plan_id, project_id, plan_hash, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.reviewed_plan_id,
                    record.project_id,
                    record.plan_hash,
                    self._dump_model(record),
                    record.created_at,
                    record.updated_at,
                ),
            )
        return record

    def get_reviewed_plan(self, reviewed_plan_id: str) -> ReviewedPlanRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM reviewed_plans WHERE reviewed_plan_id = ?",
                (reviewed_plan_id,),
            ).fetchone()
        return ReviewedPlanRecord(**json.loads(row["payload"])) if row else None

    def find_reviewed_plan(
        self,
        project_id: str,
        plan_hash: str,
    ) -> ReviewedPlanRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM reviewed_plans
                WHERE project_id = ? AND plan_hash = ?
                """,
                (project_id, plan_hash),
            ).fetchone()
        return ReviewedPlanRecord(**json.loads(row["payload"])) if row else None

    def list_reviewed_plans(self, project_id: str) -> list[ReviewedPlanRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM reviewed_plans
                WHERE project_id = ?
                ORDER BY updated_at DESC, reviewed_plan_id DESC
                """,
                (project_id,),
            ).fetchall()
        return [ReviewedPlanRecord(**json.loads(row["payload"])) for row in rows]

    def update_reviewed_plan(
        self,
        reviewed_plan_id: str,
        **updates: object,
    ) -> ReviewedPlanRecord | None:
        current = self.get_reviewed_plan(reviewed_plan_id)
        if current is None:
            return None
        payload = current.model_dump()
        payload.update(updates)
        payload["updated_at"] = str(updates.get("updated_at") or utc_now_iso())
        updated = ReviewedPlanRecord(**payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE reviewed_plans SET payload = ?, updated_at = ?
                WHERE reviewed_plan_id = ?
                """,
                (self._dump_model(updated), updated.updated_at, reviewed_plan_id),
            )
        return updated

    def add_run_link(self, record: RunLinkRecord) -> RunLinkRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO run_links
                    (run_link_id, project_id, reviewed_plan_id, run_id, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_link_id,
                    record.project_id,
                    record.reviewed_plan_id,
                    record.run_id,
                    self._dump_model(record),
                    record.created_at,
                    record.updated_at,
                ),
            )
            if str(record.status).upper() in {
                "SUCCEEDED",
                "SUCCESS",
                "COMPLETED",
                "FAILED",
                "CANCELED",
                "CANCELLED",
                "PARTIAL",
            }:
                self._append_memory_outbox(
                    conn,
                    project_id=record.project_id,
                    source_type="run_summary",
                    source_id=record.run_link_id,
                    source_hash=stable_hash(record.model_dump(mode="json")),
                    source_trust_class="authoritative_structured",
                    occurred_at=record.updated_at,
                )
        return record

    def get_run_link_by_run_id(
        self,
        project_id: str,
        run_id: str,
    ) -> RunLinkRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM run_links
                WHERE project_id = ? AND run_id = ?
                """,
                (project_id, run_id),
            ).fetchone()
        return RunLinkRecord(**json.loads(row["payload"])) if row else None

    def list_run_links(
        self,
        project_id: str,
        reviewed_plan_id: str | None = None,
    ) -> list[RunLinkRecord]:
        query = """
            SELECT payload FROM run_links
            WHERE project_id = ?
        """
        params: tuple[object, ...] = (project_id,)
        if reviewed_plan_id:
            query += " AND reviewed_plan_id = ?"
            params = (project_id, reviewed_plan_id)
        query += " ORDER BY updated_at DESC, run_link_id DESC"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [RunLinkRecord(**json.loads(row["payload"])) for row in rows]

    def update_run_link(
        self,
        run_link_id: str,
        **updates: object,
    ) -> RunLinkRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM run_links WHERE run_link_id = ?",
                (run_link_id,),
            ).fetchone()
            if row is None:
                return None
            payload = json.loads(row["payload"])
            payload.update(updates)
            payload["updated_at"] = str(updates.get("updated_at") or utc_now_iso())
            updated = RunLinkRecord(**payload)
            conn.execute(
                """
                UPDATE run_links SET payload = ?, updated_at = ?
                WHERE run_link_id = ?
                """,
                (self._dump_model(updated), updated.updated_at, run_link_id),
            )
            if str(updated.status).upper() in {
                "SUCCEEDED",
                "SUCCESS",
                "COMPLETED",
                "FAILED",
                "CANCELED",
                "CANCELLED",
                "PARTIAL",
            }:
                self._append_memory_outbox(
                    conn,
                    project_id=updated.project_id,
                    source_type="run_summary",
                    source_id=updated.run_link_id,
                    source_hash=stable_hash(updated.model_dump(mode="json")),
                    source_trust_class="authoritative_structured",
                    occurred_at=updated.updated_at,
                )
        return updated

    def add_execution_ticket(self, ticket: ExecutionTicket) -> ExecutionTicket:
        payload = ticket.model_dump(mode="json")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_tickets
                    (execution_ticket_id, project_id, status, payload, issued_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket.execution_ticket_id,
                    ticket.project_id,
                    ticket.status,
                    json.dumps(payload, ensure_ascii=False),
                    ticket.issued_at.isoformat(),
                    ticket.issued_at.isoformat(),
                ),
            )
        return ticket

    def add_execution_environment_snapshot(
        self, snapshot: ExecutionEnvironmentSnapshot
    ) -> ExecutionEnvironmentSnapshot:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_environment_snapshots
                    (snapshot_id, project_id, environment_hash, payload, captured_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.project_id,
                    snapshot.environment_hash,
                    self._dump_model(snapshot),
                    snapshot.captured_at.isoformat(),
                ),
            )
        return snapshot

    def get_execution_environment_snapshot(
        self, snapshot_id: str
    ) -> ExecutionEnvironmentSnapshot | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM execution_environment_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        return ExecutionEnvironmentSnapshot(**json.loads(row["payload"])) if row else None

    def get_execution_ticket(self, execution_ticket_id: str) -> ExecutionTicket | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM execution_tickets WHERE execution_ticket_id = ?",
                (execution_ticket_id,),
            ).fetchone()
        return ExecutionTicket(**json.loads(row["payload"])) if row else None

    def list_execution_tickets(self, project_id: str) -> list[ExecutionTicket]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM execution_tickets
                WHERE project_id = ? ORDER BY issued_at DESC, execution_ticket_id DESC
                """,
                (project_id,),
            ).fetchall()
        return [ExecutionTicket(**json.loads(row["payload"])) for row in rows]

    def update_execution_ticket(
        self,
        execution_ticket_id: str,
        **updates: object,
    ) -> ExecutionTicket | None:
        current = self.get_execution_ticket(execution_ticket_id)
        if current is None:
            return None
        payload = current.model_dump(mode="json")
        payload.update(updates)
        updated = ExecutionTicket(**payload)
        now = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE execution_tickets
                SET status = ?, payload = ?, updated_at = ?
                WHERE execution_ticket_id = ?
                """,
                (
                    updated.status,
                    json.dumps(updated.model_dump(mode="json"), ensure_ascii=False),
                    now,
                    execution_ticket_id,
                ),
            )
        return updated

    def consume_execution_ticket(
        self,
        execution_ticket_id: str,
        *,
        idempotency_key: str,
        consumed_at: datetime,
    ) -> tuple[ExecutionTicket | None, bool]:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM execution_tickets WHERE execution_ticket_id = ?",
                (execution_ticket_id,),
            ).fetchone()
            if row is None:
                return None, False
            current = ExecutionTicket(**json.loads(row["payload"]))
            if current.status == "consumed" and current.idempotency_key == idempotency_key:
                return current, False
            if current.status != "issued":
                return current, False
            updated = current.model_copy(
                update={
                    "status": "consumed",
                    "consumed_at": consumed_at,
                    "idempotency_key": idempotency_key,
                }
            )
            conn.execute(
                """
                UPDATE execution_tickets
                SET status = ?, payload = ?, updated_at = ?
                WHERE execution_ticket_id = ? AND status = 'issued'
                """,
                (
                    updated.status,
                    self._dump_model(updated),
                    consumed_at.isoformat(),
                    execution_ticket_id,
                ),
            )
            return updated, True

    def add_execution_ticket_event(
        self,
        event: ExecutionTicketEvent,
    ) -> ExecutionTicketEvent:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO execution_ticket_events
                    (event_id, execution_ticket_id, project_id, event_type, payload, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.execution_ticket_id,
                    event.project_id,
                    event.event_type,
                    json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                    event.occurred_at.isoformat(),
                ),
            )
        return event

    def list_execution_ticket_events(
        self,
        execution_ticket_id: str,
    ) -> list[ExecutionTicketEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM execution_ticket_events
                WHERE execution_ticket_id = ? ORDER BY rowid
                """,
                (execution_ticket_id,),
            ).fetchall()
        return [ExecutionTicketEvent(**json.loads(row["payload"])) for row in rows]

    def add_gateway_dispatch(self, dispatch: GatewayDispatch) -> GatewayDispatch:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO gateway_dispatches
                        (dispatch_id, command_id, execution_ticket_id, project_id, payload, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dispatch.dispatch_id,
                        dispatch.command_id,
                        dispatch.execution_ticket_id,
                        dispatch.project_id,
                        self._dump_model(dispatch),
                        dispatch.created_at.isoformat(),
                    ),
                )
                return dispatch
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT payload FROM gateway_dispatches
                    WHERE command_id = ? OR execution_ticket_id = ?
                    ORDER BY rowid LIMIT 1
                    """,
                    (dispatch.command_id, dispatch.execution_ticket_id),
                ).fetchone()
                if row is None:
                    raise
                existing = GatewayDispatch(**json.loads(row["payload"]))
                if existing.canonical_hash != dispatch.canonical_hash:
                    raise
                return existing

    def get_gateway_dispatch(self, dispatch_id: str) -> GatewayDispatch | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM gateway_dispatches WHERE dispatch_id = ?",
                (dispatch_id,),
            ).fetchone()
        return GatewayDispatch(**json.loads(row["payload"])) if row else None

    def get_gateway_dispatch_by_command(self, command_id: str) -> GatewayDispatch | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM gateway_dispatches WHERE command_id = ?",
                (command_id,),
            ).fetchone()
        return GatewayDispatch(**json.loads(row["payload"])) if row else None

    def get_gateway_dispatch_by_ticket(
        self, execution_ticket_id: str
    ) -> GatewayDispatch | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM gateway_dispatches WHERE execution_ticket_id = ?",
                (execution_ticket_id,),
            ).fetchone()
        return GatewayDispatch(**json.loads(row["payload"])) if row else None

    def add_gateway_dispatch_event(
        self, event: GatewayDispatchEvent
    ) -> GatewayDispatchEvent:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gateway_dispatch_events
                    (event_id, dispatch_id, project_id, event_type, payload, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.dispatch_id,
                    event.project_id,
                    event.event_type,
                    self._dump_model(event),
                    event.occurred_at.isoformat(),
                ),
            )
        return event

    def list_gateway_dispatch_events(
        self, dispatch_id: str
    ) -> list[GatewayDispatchEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM gateway_dispatch_events
                WHERE dispatch_id = ? ORDER BY rowid
                """,
                (dispatch_id,),
            ).fetchall()
        return [GatewayDispatchEvent(**json.loads(row["payload"])) for row in rows]

    def add_sandbox_attempt(self, attempt: SandboxAttemptRecord) -> SandboxAttemptRecord:
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO sandbox_attempts
                        (sandbox_id, project_id, run_id, dispatch_id, execution_ticket_id, status, payload, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.sandbox_id, attempt.project_id, attempt.run_id,
                        attempt.dispatch_id, attempt.execution_ticket_id, attempt.status,
                        self._dump_model(attempt), utc_now_iso(),
                    ),
                )
                return attempt
            except sqlite3.IntegrityError:
                current = self.get_sandbox_attempt(attempt.sandbox_id)
                if current is None or any(
                    getattr(current, field) != getattr(attempt, field)
                    for field in (
                        "project_id",
                        "run_id",
                        "node_id",
                        "subject_id",
                        "attempt_id",
                        "execution_ticket_id",
                        "dispatch_id",
                        "policy_hash",
                    )
                ):
                    raise
                return current

    def get_sandbox_attempt(self, sandbox_id: str) -> SandboxAttemptRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM sandbox_attempts WHERE sandbox_id = ?", (sandbox_id,)
            ).fetchone()
        return SandboxAttemptRecord(**json.loads(row["payload"])) if row else None

    def update_sandbox_attempt(
        self, sandbox_id: str, **updates: object
    ) -> SandboxAttemptRecord | None:
        current = self.get_sandbox_attempt(sandbox_id)
        if current is None:
            return None
        payload = current.model_dump(mode="json")
        payload.update(updates)
        updated = SandboxAttemptRecord(**payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE sandbox_attempts SET status = ?, payload = ?, updated_at = ? WHERE sandbox_id = ?",
                (updated.status, self._dump_model(updated), utc_now_iso(), sandbox_id),
            )
        return updated

    def list_sandbox_attempts_for_run(
        self, project_id: str, run_id: str
    ) -> list[SandboxAttemptRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM sandbox_attempts WHERE project_id = ? AND run_id = ?
                   ORDER BY rowid""",
                (project_id, run_id),
            ).fetchall()
        return [SandboxAttemptRecord(**json.loads(row["payload"])) for row in rows]

    def list_incomplete_sandbox_attempts(self) -> list[SandboxAttemptRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM sandbox_attempts
                   WHERE status IN ('PREPARING', 'PREPARED', 'RUNNING') ORDER BY rowid"""
            ).fetchall()
        return [SandboxAttemptRecord(**json.loads(row["payload"])) for row in rows]

    def create_agent_lifecycle(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
    ) -> AgentLifecycleRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_lifecycles
                    (lifecycle_id, project_id, state, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.lifecycle_id,
                    record.project_id,
                    record.state,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._insert_agent_lifecycle_event(conn, event)
        return record

    @staticmethod
    def _insert_agent_task_wake(conn, record: AgentTaskWakeRecord) -> None:
        conn.execute(
            """
            INSERT INTO agent_task_wake_outbox
                (wake_id, project_id, lifecycle_id, step_key, reason, status,
                 attempts, available_at, lease_owner, lease_expires_at,
                 last_error_code, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lifecycle_id, step_key) DO NOTHING
            """,
            (
                record.wake_id, record.project_id, record.lifecycle_id, record.step_key,
                record.reason, record.status, record.attempts, record.available_at.isoformat(),
                record.lease_owner,
                record.lease_expires_at.isoformat() if record.lease_expires_at else None,
                record.last_error_code, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                record.created_at.isoformat(), record.updated_at.isoformat(),
            ),
        )

    def create_agent_lifecycle_with_wake(
        self, record: AgentLifecycleRecord, event: AgentLifecycleEvent, wake: AgentTaskWakeRecord
    ) -> AgentLifecycleRecord:
        """Commit lifecycle creation, audit event, and planning wake together."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO agent_lifecycles
                   (lifecycle_id, project_id, state, payload, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (record.lifecycle_id, record.project_id, record.state,
                 json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                 record.created_at.isoformat(), record.updated_at.isoformat()),
            )
            self._insert_agent_lifecycle_event(conn, event)
            self._insert_agent_task_wake(conn, wake)
        return record

    def add_observation(self, record: ObservationRecord) -> ObservationRecord:
        """Append one immutable observation snapshot."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO observations
                    (observation_id, project_id, lifecycle_id, run_id,
                     observation_hash, payload, collected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.observation_id,
                    record.bindings.project_id,
                    record.bindings.lifecycle_id,
                    record.bindings.run_id,
                    record.observation_hash,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.collected_at.isoformat(),
                ),
            )
            self._append_memory_outbox(
                conn,
                project_id=record.bindings.project_id,
                source_type="observation",
                source_id=record.observation_id,
                source_hash=record.observation_hash,
                source_trust_class="authoritative_structured",
                occurred_at=record.collected_at.isoformat(),
            )
        return record

    def get_observation(self, observation_id: str) -> ObservationRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        return ObservationRecord(**json.loads(row["payload"])) if row else None

    def list_observations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
        run_id: str | None = None,
    ) -> list[ObservationRecord]:
        clauses = ["project_id = ?"]
        params: list[str] = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        query = (
            "SELECT payload FROM observations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY collected_at DESC, observation_id DESC"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [ObservationRecord(**json.loads(row["payload"])) for row in rows]

    def add_goal_evaluation(
        self,
        record: GoalEvaluationRecord,
    ) -> GoalEvaluationRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goal_evaluations
                    (goal_evaluation_id, project_id, lifecycle_id, observation_id,
                     goal_contract_id, goal_evaluation_hash, status, payload, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.goal_evaluation_id,
                    record.project_id,
                    record.lifecycle_id,
                    record.observation_id,
                    record.goal_contract_id,
                    record.goal_evaluation_hash,
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.evaluated_at.isoformat(),
                ),
            )
            self._append_memory_outbox(
                conn,
                project_id=record.project_id,
                source_type="goal_evaluation",
                source_id=record.goal_evaluation_id,
                source_hash=record.goal_evaluation_hash,
                source_trust_class="authoritative_structured",
                occurred_at=record.evaluated_at.isoformat(),
            )
        return record

    def get_goal_evaluation(
        self,
        goal_evaluation_id: str,
    ) -> GoalEvaluationRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM goal_evaluations WHERE goal_evaluation_id = ?",
                (goal_evaluation_id,),
            ).fetchone()
        return GoalEvaluationRecord(**json.loads(row["payload"])) if row else None

    def list_goal_evaluations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
        observation_id: str | None = None,
    ) -> list[GoalEvaluationRecord]:
        clauses = ["project_id = ?"]
        params: list[str] = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        if observation_id is not None:
            clauses.append("observation_id = ?")
            params.append(observation_id)
        query = (
            "SELECT payload FROM goal_evaluations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY evaluated_at DESC, goal_evaluation_id DESC"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [GoalEvaluationRecord(**json.loads(row["payload"])) for row in rows]

    def add_recovery_diagnosis(self, record: DiagnosisRecord) -> DiagnosisRecord:
        """Append one immutable diagnosis bound to immutable evidence."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recovery_diagnoses
                    (diagnosis_id, project_id, lifecycle_id, goal_evaluation_id,
                     diagnosis_hash, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.diagnosis_id,
                    record.bindings.project_id,
                    record.bindings.lifecycle_id,
                    record.bindings.goal_evaluation_id,
                    record.diagnosis_hash,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def get_recovery_diagnosis(self, diagnosis_id: str) -> DiagnosisRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_diagnoses WHERE diagnosis_id = ?",
                (diagnosis_id,),
            ).fetchone()
        return DiagnosisRecord(**json.loads(row["payload"])) if row else None

    def list_recovery_diagnoses(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[DiagnosisRecord]:
        clauses = ["project_id = ?"]
        params = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        query = (
            "SELECT payload FROM recovery_diagnoses WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, diagnosis_id DESC"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [DiagnosisRecord(**json.loads(row["payload"])) for row in rows]

    def add_recovery_proposal(self, record: RecoveryProposal) -> RecoveryProposal:
        """Append one immutable proposal; it conveys no ticket authority."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recovery_proposals
                    (recovery_proposal_id, project_id, lifecycle_id, diagnosis_id,
                     recovery_proposal_hash, recommended_candidate_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.recovery_proposal_id,
                    record.bindings.project_id,
                    record.bindings.lifecycle_id,
                    record.diagnosis_id,
                    record.recovery_proposal_hash,
                    record.recommended_candidate_id,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def get_recovery_proposal(self, proposal_id: str) -> RecoveryProposal | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_proposals WHERE recovery_proposal_id = ?",
                (proposal_id,),
            ).fetchone()
        return RecoveryProposal(**json.loads(row["payload"])) if row else None

    def list_recovery_proposals(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[RecoveryProposal]:
        clauses = ["project_id = ?"]
        params = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        query = (
            "SELECT payload FROM recovery_proposals WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, recovery_proposal_id DESC"
        )
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [RecoveryProposal(**json.loads(row["payload"])) for row in rows]

    def add_recovery_approval(
        self,
        record: RecoveryApprovalRecord,
        event: RecoveryApprovalEvent,
    ) -> RecoveryApprovalRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recovery_approvals
                    (recovery_approval_id, project_id, lifecycle_id,
                     recovery_proposal_id, candidate_id, command_id, status,
                     payload, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.recovery_approval_id,
                    record.project_id,
                    record.lifecycle_id,
                    record.recovery_proposal_id,
                    record.candidate_id,
                    record.command_id,
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.approved_at.isoformat(),
                ),
            )
            self._insert_recovery_approval_event(conn, event)
        return record

    @staticmethod
    def _insert_recovery_approval_event(
        conn: sqlite3.Connection,
        event: RecoveryApprovalEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO recovery_approval_events
                (event_id, recovery_approval_id, project_id, command_id,
                 event_type, payload, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.recovery_approval_id,
                event.project_id,
                event.command_id,
                event.event_type,
                json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                event.occurred_at.isoformat(),
            ),
        )

    def get_recovery_approval(self, approval_id: str) -> RecoveryApprovalRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_approvals WHERE recovery_approval_id = ?",
                (approval_id,),
            ).fetchone()
        return RecoveryApprovalRecord(**json.loads(row["payload"])) if row else None

    def list_recovery_approvals(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[RecoveryApprovalRecord]:
        clauses = ["project_id = ?"]
        params = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM recovery_approvals WHERE "
                + " AND ".join(clauses)
                + " ORDER BY approved_at DESC, recovery_approval_id DESC",
                tuple(params),
            ).fetchall()
        return [RecoveryApprovalRecord(**json.loads(row["payload"])) for row in rows]

    def list_recovery_approval_events(
        self,
        approval_id: str,
    ) -> list[RecoveryApprovalEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM recovery_approval_events
                WHERE recovery_approval_id = ? ORDER BY occurred_at, event_id
                """,
                (approval_id,),
            ).fetchall()
        return [RecoveryApprovalEvent(**json.loads(row["payload"])) for row in rows]

    def update_recovery_approval(
        self,
        record: RecoveryApprovalRecord,
        event: RecoveryApprovalEvent,
        *,
        expected_status: str,
    ) -> RecoveryApprovalRecord:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE recovery_approvals SET status = ?, payload = ?
                WHERE recovery_approval_id = ? AND status = ?
                """,
                (
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.recovery_approval_id,
                    expected_status,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("RECOVERY_APPROVAL_CONCURRENT_UPDATE")
            self._insert_recovery_approval_event(conn, event)
        return record

    def create_recovery_attempt(
        self,
        record: RecoveryAttemptRecord,
        event: RecoveryAttemptEvent,
    ) -> RecoveryAttemptRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO recovery_attempts
                    (recovery_attempt_id, project_id, lifecycle_id,
                     recovery_proposal_id, candidate_id, command_id,
                     idempotency_key, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.recovery_attempt_id,
                    record.project_id,
                    record.lifecycle_id,
                    record.recovery_proposal_id,
                    record.candidate_id,
                    record.command_id,
                    record.idempotency_key,
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._insert_recovery_attempt_event(conn, event)
        return record

    @staticmethod
    def _insert_recovery_attempt_event(
        conn: sqlite3.Connection,
        event: RecoveryAttemptEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO recovery_attempt_events
                (event_id, recovery_attempt_id, project_id, lifecycle_id,
                 command_id, event_type, payload, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.recovery_attempt_id,
                event.project_id,
                event.lifecycle_id,
                event.command_id,
                event.event_type,
                json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                event.occurred_at.isoformat(),
            ),
        )

    def get_recovery_attempt(self, attempt_id: str) -> RecoveryAttemptRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_attempts WHERE recovery_attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return RecoveryAttemptRecord(**json.loads(row["payload"])) if row else None

    def get_recovery_attempt_by_idempotency(
        self,
        idempotency_key: str,
    ) -> RecoveryAttemptRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_attempts WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return RecoveryAttemptRecord(**json.loads(row["payload"])) if row else None

    def list_recovery_attempts(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[RecoveryAttemptRecord]:
        clauses = ["project_id = ?"]
        params = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM recovery_attempts WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, recovery_attempt_id DESC",
                tuple(params),
            ).fetchall()
        return [RecoveryAttemptRecord(**json.loads(row["payload"])) for row in rows]

    def list_recovery_attempt_events(
        self,
        attempt_id: str,
    ) -> list[RecoveryAttemptEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM recovery_attempt_events
                WHERE recovery_attempt_id = ? ORDER BY occurred_at, event_id
                """,
                (attempt_id,),
            ).fetchall()
        return [RecoveryAttemptEvent(**json.loads(row["payload"])) for row in rows]

    def transition_recovery_attempt(
        self,
        record: RecoveryAttemptRecord,
        event: RecoveryAttemptEvent,
        *,
        expected_status: str,
    ) -> RecoveryAttemptRecord:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE recovery_attempts
                SET status = ?, payload = ?, updated_at = ?
                WHERE recovery_attempt_id = ? AND status = ?
                """,
                (
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.updated_at.isoformat(),
                    record.recovery_attempt_id,
                    expected_status,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("RECOVERY_ATTEMPT_CONCURRENT_TRANSITION")
            self._insert_recovery_attempt_event(conn, event)
        return record

    def reserve_recovery_quota(
        self,
        reservation: RecoveryQuotaReservation,
    ) -> RecoveryQuotaReservation:
        """Atomically check every hard dimension and reserve it once."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM recovery_quota_reservations
                WHERE project_id = ? AND lifecycle_id = ?
                  AND status IN ('reserved', 'consumed')
                """,
                (reservation.project_id, reservation.lifecycle_id),
            ).fetchall()
            existing = [
                RecoveryQuotaReservation(**json.loads(row["payload"])) for row in rows
            ]
            limits = reservation.effective_limits
            if len(existing) + 1 > limits["max_lifecycle_recovery_attempts"]:
                raise RuntimeError("RECOVERY_QUOTA_LIFECYCLE_EXCEEDED")
            for node_id in reservation.node_ids:
                count = sum(node_id in item.node_ids for item in existing)
                if count + 1 > limits["max_node_attempts"]:
                    raise RuntimeError("RECOVERY_QUOTA_NODE_EXCEEDED")
            for node_id in reservation.node_ids:
                for subject_id in reservation.subject_ids:
                    count = sum(
                        node_id in item.node_ids and subject_id in item.subject_ids
                        for item in existing
                    )
                    if count + 1 > limits["max_subject_node_attempts"]:
                        raise RuntimeError("RECOVERY_QUOTA_SUBJECT_NODE_EXCEEDED")
            replan_count = sum(item.reserves_replan for item in existing)
            if reservation.reserves_replan and replan_count + 1 > limits["max_replans"]:
                raise RuntimeError("RECOVERY_QUOTA_REPLAN_EXCEEDED")
            wall_total = sum(item.reserved_wall_seconds for item in existing)
            if wall_total + reservation.reserved_wall_seconds > limits["max_recovery_wall_seconds"]:
                raise RuntimeError("RECOVERY_QUOTA_WALL_EXCEEDED")
            conn.execute(
                """
                INSERT INTO recovery_quota_reservations
                    (reservation_id, project_id, lifecycle_id,
                     recovery_attempt_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation.reservation_id,
                    reservation.project_id,
                    reservation.lifecycle_id,
                    reservation.recovery_attempt_id,
                    reservation.status,
                    json.dumps(reservation.model_dump(mode="json"), ensure_ascii=False),
                    reservation.created_at.isoformat(),
                    reservation.created_at.isoformat(),
                ),
            )
        return reservation

    def get_recovery_quota_reservation(
        self,
        reservation_id: str,
    ) -> RecoveryQuotaReservation | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM recovery_quota_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
        return RecoveryQuotaReservation(**json.loads(row["payload"])) if row else None

    def list_recovery_quota_reservations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[RecoveryQuotaReservation]:
        clauses = ["project_id = ?"]
        params = [project_id]
        if lifecycle_id is not None:
            clauses.append("lifecycle_id = ?")
            params.append(lifecycle_id)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM recovery_quota_reservations WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at, reservation_id",
                tuple(params),
            ).fetchall()
        return [RecoveryQuotaReservation(**json.loads(row["payload"])) for row in rows]

    def update_recovery_quota_reservation(
        self,
        record: RecoveryQuotaReservation,
        *,
        expected_status: str,
    ) -> RecoveryQuotaReservation:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE recovery_quota_reservations
                SET status = ?, payload = ?, updated_at = ?
                WHERE reservation_id = ? AND status = ?
                """,
                (
                    record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    (record.consumed_at or record.released_at or record.created_at).isoformat(),
                    record.reservation_id,
                    expected_status,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("RECOVERY_QUOTA_RESERVATION_CONCURRENT_UPDATE")
        return record

    def _insert_agent_lifecycle_event(
        self,
        conn: sqlite3.Connection,
        event: AgentLifecycleEvent,
    ) -> None:
        conn.execute(
            """
            INSERT INTO agent_lifecycle_events
                (event_id, lifecycle_id, project_id, command_id, from_state,
                 to_state, payload, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.lifecycle_id,
                event.project_id,
                event.command_id,
                event.from_state,
                event.to_state,
                json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
                event.occurred_at.isoformat(),
            ),
        )
        self._append_memory_outbox(
            conn,
            project_id=event.project_id,
            source_type="agent_lifecycle_event",
            source_id=event.event_id,
            source_hash=stable_hash(event.model_dump(mode="json")),
            source_trust_class="authoritative_structured",
            occurred_at=event.occurred_at.isoformat(),
        )

    def get_agent_lifecycle(self, lifecycle_id: str) -> AgentLifecycleRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_lifecycles WHERE lifecycle_id = ?",
                (lifecycle_id,),
            ).fetchone()
        return _decode_agent_lifecycle(row["payload"]) if row else None

    def add_agent_evidence_snapshot(self, record: EvidenceSnapshot) -> EvidenceSnapshot:
        """Persist immutable redacted evidence by its canonical hash."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM agent_evidence_snapshots WHERE snapshot_hash = ?",
                (record.snapshot_hash,),
            ).fetchone()
            if existing:
                current = EvidenceSnapshot(**json.loads(existing["payload"]))
                if current.project_id != record.project_id or current.lifecycle_id != record.lifecycle_id:
                    raise ValueError("AGENT_EVIDENCE_SNAPSHOT_HASH_COLLISION")
                return current
            conn.execute(
                """
                INSERT INTO agent_evidence_snapshots
                    (snapshot_hash, project_id, lifecycle_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record.snapshot_hash, record.project_id, record.lifecycle_id,
                 self._dump_model(record), record.created_at.isoformat()),
            )
        return record

    def get_agent_evidence_snapshot(self, snapshot_hash: str) -> EvidenceSnapshot | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_evidence_snapshots WHERE snapshot_hash = ?",
                (snapshot_hash,),
            ).fetchone()
        return EvidenceSnapshot(**json.loads(row["payload"])) if row else None

    def create_agent_harness_attempt(self, record: AgentHarnessAttempt) -> AgentHarnessAttempt:
        """Create the one Harness attempt bound to a lifecycle.

        The unique lifecycle key makes duplicate command delivery fail closed
        instead of starting a second control loop.
        """
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_harness_attempts
                    (attempt_id, lifecycle_id, project_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.attempt_id, record.lifecycle_id, record.project_id, record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(), record.updated_at.isoformat(),
                ),
            )
        return record

    def get_agent_harness_attempt(self, lifecycle_id: str) -> AgentHarnessAttempt | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_harness_attempts WHERE lifecycle_id=?", (lifecycle_id,)
            ).fetchone()
        return AgentHarnessAttempt(**json.loads(row["payload"])) if row else None

    def update_agent_harness_attempt(
        self,
        record: AgentHarnessAttempt,
        *,
        expected_status: str,
        expected_step_no: int | None = None,
        expected_context_hash: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> AgentHarnessAttempt:
        predicates = ["attempt_id=?", "project_id=?", "status=?"]
        parameters: list[object] = [record.attempt_id, record.project_id, expected_status]
        if expected_step_no is not None:
            predicates.append("json_extract(payload, '$.next_step_no')=?")
            parameters.append(expected_step_no)
        if expected_context_hash is not None:
            predicates.append("json_extract(payload, '$.context_hash')=?")
            parameters.append(expected_context_hash)
        if expected_lease_owner is not None:
            predicates.append("json_extract(payload, '$.lease_owner')=?")
            parameters.append(expected_lease_owner)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "UPDATE agent_harness_attempts SET status=?, payload=?, updated_at=? WHERE "
                + " AND ".join(predicates),
                [
                    record.status, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.updated_at.isoformat(), *parameters,
                ],
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_HARNESS_ATTEMPT_CONCURRENT_UPDATE")
        return record

    def add_agent_harness_context(self, record: AgentHarnessContext) -> AgentHarnessContext:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO agent_harness_contexts
                    (context_hash, lifecycle_id, project_id, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.context_hash, record.lifecycle_id, record.project_id,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False), record.created_at.isoformat(),
                ),
            )
        return record

    def get_agent_harness_context(self, context_hash: str) -> AgentHarnessContext | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_harness_contexts WHERE context_hash=?", (context_hash,)
            ).fetchone()
        return AgentHarnessContext(**json.loads(row["payload"])) if row else None

    def add_agent_harness_step(self, record: AgentHarnessStep) -> AgentHarnessStep:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_harness_steps
                    (step_id, attempt_id, project_id, step_no, idempotency_key, payload, started_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.step_id, record.attempt_id, record.project_id, record.step_no,
                    record.idempotency_key, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.started_at.isoformat(),
                    record.completed_at.isoformat() if record.completed_at else None,
                ),
            )
        return record

    def get_agent_harness_step_by_idempotency(self, idempotency_key: str) -> AgentHarnessStep | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_harness_steps WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
        return AgentHarnessStep(**json.loads(row["payload"])) if row else None

    def update_agent_harness_step(self, record: AgentHarnessStep) -> AgentHarnessStep:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_harness_steps SET payload=?, completed_at=?
                WHERE step_id=? AND attempt_id=? AND project_id=?
                """,
                (
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.completed_at.isoformat() if record.completed_at else None,
                    record.step_id, record.attempt_id, record.project_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_HARNESS_STEP_CONCURRENT_UPDATE")
        return record

    def list_agent_harness_steps(self, attempt_id: str) -> list[AgentHarnessStep]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM agent_harness_steps WHERE attempt_id=? ORDER BY step_no", (attempt_id,)
            ).fetchall()
        return [AgentHarnessStep(**json.loads(row["payload"])) for row in rows]

    def add_agent_harness_action(self, record: AgentActionRecord) -> AgentActionRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_harness_actions
                    (action_id, attempt_id, step_id, status, payload, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.action_id, record.attempt_id, record.step_id, record.status,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(),
                    record.completed_at.isoformat() if record.completed_at else None,
                ),
            )
        return record

    def update_agent_harness_action(
        self, record: AgentActionRecord, *, expected_status: str
    ) -> AgentActionRecord:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_harness_actions SET status=?, payload=?, completed_at=?
                WHERE action_id=? AND attempt_id=? AND step_id=? AND status=?
                """,
                (
                    record.status, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.completed_at.isoformat() if record.completed_at else None,
                    record.action_id, record.attempt_id, record.step_id, expected_status,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_HARNESS_ACTION_CONCURRENT_UPDATE")
        return record

    def list_agent_harness_actions(self, attempt_id: str) -> list[AgentActionRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM agent_harness_actions WHERE attempt_id=? ORDER BY created_at, action_id",
                (attempt_id,),
            ).fetchall()
        return [AgentActionRecord(**json.loads(row["payload"])) for row in rows]

    def add_agent_invariant_audit(
        self, record: AgentInvariantAuditRecord
    ) -> AgentInvariantAuditRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_invariant_audits
                    (audit_id, lifecycle_id, project_id, report_hash, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.audit_id,
                    record.lifecycle_id,
                    record.project_id,
                    record.report_hash,
                    self._dump_model(record),
                    record.created_at.isoformat(),
                ),
            )
        return record

    def list_agent_invariant_audits(
        self, lifecycle_id: str
    ) -> list[AgentInvariantAuditRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM agent_invariant_audits
                WHERE lifecycle_id = ? ORDER BY created_at, audit_id
                """,
                (lifecycle_id,),
            ).fetchall()
        return [AgentInvariantAuditRecord(**json.loads(row["payload"])) for row in rows]

    def enqueue_agent_task_wake(self, record: AgentTaskWakeRecord) -> AgentTaskWakeRecord:
        """Persist a planning wake before any in-memory notification occurs.

        ``step_key`` is supplied by the caller from durable lifecycle state, so
        repeated command delivery cannot create a second unit of planning work.
        """
        with self._lock, self._connect() as conn:
            existing_row = conn.execute(
                "SELECT payload FROM agent_task_wake_outbox WHERE lifecycle_id=? AND step_key=?",
                (record.lifecycle_id, record.step_key),
            ).fetchone()
            if existing_row is not None:
                existing = AgentTaskWakeRecord(**json.loads(existing_row["payload"]))
                record = existing.model_copy(update={
                    "reason": record.reason,
                    "status": "PENDING" if existing.status == "CONSUMED" else existing.status,
                    "available_at": record.available_at if existing.status == "CONSUMED" else existing.available_at,
                    "lease_owner": None if existing.status == "CONSUMED" else existing.lease_owner,
                    "lease_expires_at": None if existing.status == "CONSUMED" else existing.lease_expires_at,
                    "updated_at": record.updated_at,
                })
            conn.execute(
                """
                INSERT INTO agent_task_wake_outbox
                    (wake_id, project_id, lifecycle_id, step_key, reason, status,
                     attempts, available_at, lease_owner, lease_expires_at,
                     last_error_code, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lifecycle_id, step_key) DO UPDATE SET
                    reason=excluded.reason,
                    status=CASE WHEN agent_task_wake_outbox.status='CONSUMED'
                                THEN 'PENDING' ELSE agent_task_wake_outbox.status END,
                    available_at=CASE WHEN agent_task_wake_outbox.status='CONSUMED'
                                      THEN excluded.available_at ELSE agent_task_wake_outbox.available_at END,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (
                    record.wake_id, record.project_id, record.lifecycle_id, record.step_key,
                    record.reason, record.status, record.attempts, record.available_at.isoformat(),
                    record.lease_owner,
                    record.lease_expires_at.isoformat() if record.lease_expires_at else None,
                    record.last_error_code,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(), record.updated_at.isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM agent_task_wake_outbox WHERE lifecycle_id=? AND step_key=?",
                (record.lifecycle_id, record.step_key),
            ).fetchone()
        return AgentTaskWakeRecord(**json.loads(row["payload"]))

    def claim_next_agent_task_wake(
        self, *, owner: str, now: datetime, lease_expires_at: datetime
    ) -> AgentTaskWakeRecord | None:
        """Atomically claim one due outbox row; competing schedulers lose safely."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT wake_id, payload FROM agent_task_wake_outbox
                WHERE (status IN ('PENDING', 'RETRY') AND available_at <= ?)
                   OR (status = 'CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                ORDER BY available_at, created_at, wake_id LIMIT 1
                """,
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            record = AgentTaskWakeRecord(**json.loads(row["payload"]))
            claimed = record.model_copy(update={
                "status": "CLAIMED", "attempts": record.attempts + 1,
                "lease_owner": owner, "lease_expires_at": lease_expires_at,
                "updated_at": now,
            })
            cursor = conn.execute(
                """
                UPDATE agent_task_wake_outbox
                SET status=?, attempts=?, lease_owner=?, lease_expires_at=?, payload=?, updated_at=?
                WHERE wake_id=? AND (
                    (status IN ('PENDING', 'RETRY') AND available_at <= ?)
                    OR (status = 'CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                )
                """,
                (
                    claimed.status, claimed.attempts, claimed.lease_owner,
                    claimed.lease_expires_at.isoformat(),
                    json.dumps(claimed.model_dump(mode="json"), ensure_ascii=False),
                    claimed.updated_at.isoformat(), claimed.wake_id, now.isoformat(), now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
        return claimed

    def next_agent_task_wake_at(self) -> datetime | None:
        """Return the next durable retry or expired lease time without claiming it."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT MIN(CASE WHEN status='CLAIMED' THEN lease_expires_at ELSE available_at END) AS due_at
                   FROM agent_task_wake_outbox
                   WHERE status IN ('PENDING', 'RETRY')
                      OR (status='CLAIMED' AND lease_expires_at IS NOT NULL)"""
            ).fetchone()
        return datetime.fromisoformat(row["due_at"]) if row and row["due_at"] else None

    def complete_agent_task_wake(self, record: AgentTaskWakeRecord, *, owner: str, now: datetime) -> AgentTaskWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TASK_WAKE_OWNER_MISMATCH")
        completed = record.model_copy(update={
            "status": "CONSUMED", "lease_owner": None, "lease_expires_at": None,
            "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_task_wake_outbox
                   SET status=?, lease_owner=NULL, lease_expires_at=NULL, payload=?, updated_at=?
                   WHERE wake_id=? AND status='CLAIMED' AND lease_owner=?""",
                (completed.status, json.dumps(completed.model_dump(mode="json"), ensure_ascii=False),
                 completed.updated_at.isoformat(), completed.wake_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TASK_WAKE_CONCURRENT_UPDATE")
        return completed

    def retry_agent_task_wake(
        self, record: AgentTaskWakeRecord, *, owner: str, now: datetime,
        available_at: datetime, error_code: str,
    ) -> AgentTaskWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TASK_WAKE_OWNER_MISMATCH")
        retried = record.model_copy(update={
            "status": "RETRY", "available_at": available_at, "lease_owner": None,
            "lease_expires_at": None, "last_error_code": error_code[:128], "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_task_wake_outbox
                   SET status=?, available_at=?, lease_owner=NULL, lease_expires_at=NULL,
                       last_error_code=?, payload=?, updated_at=?
                   WHERE wake_id=? AND status='CLAIMED' AND lease_owner=?""",
                (retried.status, retried.available_at.isoformat(), retried.last_error_code,
                 json.dumps(retried.model_dump(mode="json"), ensure_ascii=False),
                 retried.updated_at.isoformat(), retried.wake_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TASK_WAKE_CONCURRENT_UPDATE")
        return retried

    def stop_agent_task_wake(
        self, record: AgentTaskWakeRecord, *, owner: str, now: datetime, error_code: str
    ) -> AgentTaskWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TASK_WAKE_OWNER_MISMATCH")
        stopped = record.model_copy(update={
            "status": "CONSUMED", "lease_owner": None, "lease_expires_at": None,
            "last_error_code": error_code[:128], "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_task_wake_outbox
                   SET status=?, lease_owner=NULL, lease_expires_at=NULL, last_error_code=?, payload=?, updated_at=?
                   WHERE wake_id=? AND status='CLAIMED' AND lease_owner=?""",
                (stopped.status, stopped.last_error_code, json.dumps(stopped.model_dump(mode="json"), ensure_ascii=False),
                 stopped.updated_at.isoformat(), stopped.wake_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TASK_WAKE_CONCURRENT_UPDATE")
        return stopped

    def list_agent_task_wakes(self, *, project_id: str, include_consumed: bool = False) -> list[AgentTaskWakeRecord]:
        predicate = "project_id=?" if include_consumed else "project_id=? AND status != 'CONSUMED'"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM agent_task_wake_outbox WHERE {predicate} ORDER BY created_at, wake_id",
                (project_id,),
            ).fetchall()
        return [AgentTaskWakeRecord(**json.loads(row["payload"])) for row in rows]

    def enqueue_agent_execution_wake(
        self, record: AgentExecutionWakeRecord
    ) -> AgentExecutionWakeRecord:
        """Persist one run-evidence check; one lifecycle/run has one live row."""
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM agent_execution_wake_outbox WHERE lifecycle_id=? AND run_id=?",
                (record.lifecycle_id, record.run_id),
            ).fetchone()
            if existing is not None:
                prior = AgentExecutionWakeRecord(**json.loads(existing["payload"]))
                record = prior.model_copy(update={
                    "status": "PENDING" if prior.status == "CONSUMED" else prior.status,
                    "available_at": record.available_at if prior.status == "CONSUMED" else min(prior.available_at, record.available_at),
                    "lease_owner": None if prior.status == "CONSUMED" else prior.lease_owner,
                    "lease_expires_at": None if prior.status == "CONSUMED" else prior.lease_expires_at,
                    "updated_at": record.updated_at,
                })
            conn.execute(
                """
                INSERT INTO agent_execution_wake_outbox
                    (wake_id, project_id, lifecycle_id, run_id, status, attempts,
                     available_at, lease_owner, lease_expires_at, last_error_code,
                     payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lifecycle_id, run_id) DO UPDATE SET
                    status=excluded.status, available_at=excluded.available_at,
                    lease_owner=excluded.lease_owner, lease_expires_at=excluded.lease_expires_at,
                    payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (
                    record.wake_id, record.project_id, record.lifecycle_id, record.run_id,
                    record.status, record.attempts, record.available_at.isoformat(),
                    record.lease_owner,
                    record.lease_expires_at.isoformat() if record.lease_expires_at else None,
                    record.last_error_code, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.created_at.isoformat(), record.updated_at.isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM agent_execution_wake_outbox WHERE lifecycle_id=? AND run_id=?",
                (record.lifecycle_id, record.run_id),
            ).fetchone()
        return AgentExecutionWakeRecord(**json.loads(row["payload"]))

    def claim_next_agent_execution_wake(
        self, *, owner: str, now: datetime, lease_expires_at: datetime
    ) -> AgentExecutionWakeRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT wake_id, payload FROM agent_execution_wake_outbox
                   WHERE (status IN ('PENDING', 'RETRY') AND available_at<=?)
                      OR (status='CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                   ORDER BY available_at, created_at, wake_id LIMIT 1""",
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            record = AgentExecutionWakeRecord(**json.loads(row["payload"]))
            claimed = record.model_copy(update={
                "status": "CLAIMED", "attempts": record.attempts + 1,
                "lease_owner": owner, "lease_expires_at": lease_expires_at, "updated_at": now,
            })
            cursor = conn.execute(
                """UPDATE agent_execution_wake_outbox
                   SET status=?, attempts=?, lease_owner=?, lease_expires_at=?, payload=?, updated_at=?
                   WHERE wake_id=? AND ((status IN ('PENDING', 'RETRY') AND available_at<=?)
                      OR (status='CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?))""",
                (
                    claimed.status, claimed.attempts, claimed.lease_owner,
                    claimed.lease_expires_at.isoformat(), json.dumps(claimed.model_dump(mode="json"), ensure_ascii=False),
                    claimed.updated_at.isoformat(), claimed.wake_id, now.isoformat(), now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
        return claimed

    def complete_agent_execution_wake(
        self, record: AgentExecutionWakeRecord, *, owner: str, now: datetime
    ) -> AgentExecutionWakeRecord:
        completed = record.model_copy(update={
            "status": "CONSUMED", "lease_owner": None, "lease_expires_at": None, "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_execution_wake_outbox
                   SET status=?, lease_owner=NULL, lease_expires_at=NULL, payload=?, updated_at=?
                   WHERE wake_id=? AND status='CLAIMED' AND lease_owner=?""",
                (completed.status, json.dumps(completed.model_dump(mode="json"), ensure_ascii=False),
                 completed.updated_at.isoformat(), completed.wake_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_EXECUTION_WAKE_CONCURRENT_UPDATE")
        return completed

    def retry_agent_execution_wake(
        self, record: AgentExecutionWakeRecord, *, owner: str, now: datetime,
        available_at: datetime, error_code: str,
    ) -> AgentExecutionWakeRecord:
        retried = record.model_copy(update={
            "status": "RETRY", "available_at": available_at, "lease_owner": None,
            "lease_expires_at": None, "last_error_code": error_code[:128], "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_execution_wake_outbox
                   SET status=?, available_at=?, lease_owner=NULL, lease_expires_at=NULL,
                       last_error_code=?, payload=?, updated_at=?
                   WHERE wake_id=? AND status='CLAIMED' AND lease_owner=?""",
                (retried.status, retried.available_at.isoformat(), retried.last_error_code,
                 json.dumps(retried.model_dump(mode="json"), ensure_ascii=False),
                 retried.updated_at.isoformat(), retried.wake_id, owner),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_EXECUTION_WAKE_CONCURRENT_UPDATE")
        return retried

    def list_agent_execution_wakes(
        self, *, project_id: str, include_consumed: bool = False
    ) -> list[AgentExecutionWakeRecord]:
        predicate = "project_id=?" if include_consumed else "project_id=? AND status != 'CONSUMED'"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM agent_execution_wake_outbox WHERE {predicate} ORDER BY available_at, wake_id",
                (project_id,),
            ).fetchall()
        return [AgentExecutionWakeRecord(**json.loads(row["payload"])) for row in rows]

    def scan_agent_recovery_page(
        self, *, consumer: RecoveryScanConsumer, now: datetime,
        project_id: str | None = None, limit: int = 100,
    ) -> AgentRecoveryScanPage:
        """Commit at most one keyset page and its missing wakes atomically.

        The fixed cycle upper bound makes continuation finite. A completed
        cycle resets on the next call so newly inserted lower IDs are covered.
        Repeated/crashed scans cannot resurrect an already consumed checkpoint.
        """
        if consumer not in RECOVERY_SCAN_STATES or not 1 <= limit <= 100:
            raise ValueError("AGENT_RECOVERY_SCAN_INVALID")
        states = RECOVERY_SCAN_STATES[consumer]
        predicate = f"state IN ({','.join('?' for _ in states)}) AND json_extract(payload, '$.schema_version')=?"
        # Unsupported historical contracts remain untouched and explicitly
        # unreadable. They cannot starve current tasks or acquire new wakes.
        args: tuple = (*states, AgentLifecycleRecord.model_fields["schema_version"].default)
        if project_id is not None:
            predicate += " AND project_id=?"
            args += (project_id,)
        scheduled: list[str] = []
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            checkpoint = conn.execute(
                "SELECT * FROM agent_recovery_scans WHERE consumer=? AND project_scope=?",
                (consumer, project_id or ""),
            ).fetchone()
            if checkpoint is None or checkpoint["completed"]:
                cursor = ""
                row = conn.execute(
                    f"SELECT lifecycle_id FROM agent_lifecycles WHERE {predicate} ORDER BY lifecycle_id DESC LIMIT 1", args,
                ).fetchone()
                upper = row[0] if row else ""
            else:
                cursor, upper = checkpoint["cursor"], checkpoint["upper_bound"]
            rows = conn.execute(
                f"SELECT payload FROM agent_lifecycles WHERE {predicate} AND lifecycle_id>? AND lifecycle_id<=? ORDER BY lifecycle_id LIMIT ?",
                (*args, cursor, upper, limit),
            ).fetchall()
            for row in rows:
                lifecycle = _decode_agent_lifecycle(row["payload"])
                cursor = lifecycle.lifecycle_id
                if consumer == "planning":
                    step_key = f"{lifecycle.state}:{lifecycle.updated_at.isoformat()}"
                    exists = conn.execute(
                        "SELECT 1 FROM agent_task_wake_outbox WHERE lifecycle_id=? AND step_key=?",
                        (lifecycle.lifecycle_id, step_key),
                    ).fetchone()
                    if exists:
                        continue
                    self._insert_agent_task_wake(conn, AgentTaskWakeRecord(
                        wake_id=f"agent_wake_{uuid4().hex}", project_id=lifecycle.project_id,
                        lifecycle_id=lifecycle.lifecycle_id, step_key=step_key,
                        reason="persistent_rescan", available_at=now, created_at=now, updated_at=now,
                    ))
                else:
                    # An empty run reference identifies a persisted intermediate
                    # checkpoint; it never claims a run was dispatched.
                    run_id = lifecycle.run_id or ""
                    exists = conn.execute(
                        "SELECT 1 FROM agent_execution_wake_outbox WHERE lifecycle_id=? AND run_id=?",
                        (lifecycle.lifecycle_id, run_id),
                    ).fetchone()
                    if exists:
                        scheduled.append(lifecycle.lifecycle_id)
                        continue
                    wake = AgentExecutionWakeRecord(
                        wake_id=f"agent_execution_wake_{uuid4().hex}",
                        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
                        run_id=run_id, available_at=now, created_at=now, updated_at=now,
                    )
                    conn.execute(
                        """INSERT INTO agent_execution_wake_outbox
                           (wake_id, project_id, lifecycle_id, run_id, status, attempts,
                            available_at, payload, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (wake.wake_id, wake.project_id, wake.lifecycle_id, wake.run_id,
                         wake.status, wake.attempts, now.isoformat(), self._dump_model(wake),
                         now.isoformat(), now.isoformat()),
                    )
                scheduled.append(lifecycle.lifecycle_id)
            has_more = conn.execute(
                f"SELECT 1 FROM agent_lifecycles WHERE {predicate} AND lifecycle_id>? AND lifecycle_id<=? LIMIT 1",
                (*args, cursor, upper),
            ).fetchone() is not None
            conn.execute(
                """INSERT INTO agent_recovery_scans VALUES (?, ?, 1, ?, ?, ?)
                   ON CONFLICT(consumer, project_scope) DO UPDATE SET
                   cursor=excluded.cursor, upper_bound=excluded.upper_bound, completed=excluded.completed""",
                (consumer, project_id or "", cursor, upper, int(not has_more)),
            )
        return AgentRecoveryScanPage(lifecycle_ids=tuple(scheduled), has_more=has_more)

    def list_agent_lifecycles(self, project_id: str) -> list[AgentLifecycleRecord]:
        schema_version = AgentLifecycleRecord.model_fields["schema_version"].default
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM agent_lifecycles
                WHERE project_id = ? AND json_extract(payload, '$.schema_version') = ?
                ORDER BY updated_at DESC, lifecycle_id DESC
                """,
                (project_id, schema_version),
            ).fetchall()
        return [_decode_agent_lifecycle(row["payload"]) for row in rows]

    def _current_project_multi_agent_consent(
        self, conn: sqlite3.Connection, project_id: str
    ) -> ProjectMultiAgentConsent | None:
        row = conn.execute(
            "SELECT payload FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if row is None:
            return None
        project = self._load_payload(row["payload"], ProjectDetail)
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        defaults = metadata.get("agent_defaults")
        raw = defaults.get("multi_agent_consent") if isinstance(defaults, dict) else None
        return (
            ProjectMultiAgentConsent.model_validate(raw)
            if isinstance(raw, dict)
            else ProjectMultiAgentConsent.default_for_project(project_id)
        )

    @staticmethod
    def _lifecycle_has_current_team_binding(
        conn: sqlite3.Connection, team: AgentTeamRecord
    ) -> bool:
        row = conn.execute(
            "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=?",
            (team.lifecycle_id, team.project_id),
        ).fetchone()
        if row is None:
            return False
        lifecycle = _decode_agent_lifecycle(row["payload"])
        return (
            lifecycle.planning_mode == "multi_agent"
            and lifecycle.planning_team_id == team.team_id
            and lifecycle.planning_team_epoch == team.team_epoch
        )

    @staticmethod
    def _insert_agent_team_event(conn: sqlite3.Connection, event: AgentTeamEvent) -> None:
        conn.execute(
            """INSERT INTO agent_team_events
               (event_id, team_id, team_epoch, project_id, lifecycle_id, command_id,
                event_type, idempotency_key, payload_hash, payload, occurred_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.team_id,
                event.team_epoch,
                event.project_id,
                event.lifecycle_id,
                event.command_id,
                event.event_type,
                event.idempotency_key,
                event.payload_hash,
                event.model_dump_json(),
                event.occurred_at.isoformat(),
            ),
        )

    @staticmethod
    def _insert_agent_team_wake(conn: sqlite3.Connection, wake: AgentTeamWakeRecord) -> None:
        conn.execute(
            """INSERT INTO agent_team_wake_outbox
               (wake_id, team_id, team_epoch, project_id, lifecycle_id, step_key,
                status, available_at, lease_owner, lease_expires_at, attempt_count,
                payload, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                wake.wake_id,
                wake.team_id,
                wake.team_epoch,
                wake.project_id,
                wake.lifecycle_id,
                wake.step_key,
                wake.status,
                wake.available_at.isoformat(),
                wake.lease_owner,
                wake.lease_expires_at.isoformat() if wake.lease_expires_at else None,
                wake.attempt_count,
                wake.model_dump_json(),
                wake.created_at.isoformat(),
                wake.updated_at.isoformat(),
            ),
        )

    def add_agent_planning_base_context(
        self, record: AgentPlanningBaseContext
    ) -> AgentPlanningBaseContext:
        with self._lock, self._connect() as conn:
            lifecycle = conn.execute(
                "SELECT 1 FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=?",
                (record.lifecycle_id, record.project_id),
            ).fetchone()
            if lifecycle is None:
                raise RuntimeError("AGENT_PLANNING_BASE_LIFECYCLE_NOT_FOUND")
            conn.execute(
                """INSERT OR IGNORE INTO agent_planning_base_contexts
                   (base_context_hash, project_id, lifecycle_id, planning_epoch, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.base_context_hash,
                    record.project_id,
                    record.lifecycle_id,
                    record.planning_epoch,
                    self._dump_model(record),
                    record.created_at.isoformat(),
                ),
            )
            row = conn.execute(
                """SELECT payload FROM agent_planning_base_contexts
                   WHERE lifecycle_id=? AND planning_epoch=?""",
                (record.lifecycle_id, record.planning_epoch),
            ).fetchone()
            persisted = (
                AgentPlanningBaseContext.model_validate_json(row["payload"])
                if row is not None else None
            )
            if (
                persisted is None
                or persisted.model_dump(mode="json", exclude={"created_at"})
                != record.model_dump(mode="json", exclude={"created_at"})
            ):
                raise RuntimeError("AGENT_PLANNING_BASE_CONTEXT_CONFLICT")
        return persisted

    def get_agent_planning_base_context(
        self, project_id: str, base_context_hash: str
    ) -> AgentPlanningBaseContext | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT payload FROM agent_planning_base_contexts
                   WHERE project_id=? AND base_context_hash=?""",
                (project_id, base_context_hash),
            ).fetchone()
        return AgentPlanningBaseContext.model_validate_json(row["payload"]) if row else None

    def create_agent_team_bundle(
        self,
        *,
        lifecycle_record: AgentLifecycleRecord,
        lifecycle_event: AgentLifecycleEvent,
        expected_team_id: str | None,
        expected_team_epoch: int,
        base_context: AgentPlanningBaseContext,
        team: AgentTeamRecord,
        context_snapshot: AgentTeamContextSnapshot,
        role_contexts: dict[AgentReviewerKind, AgentTeamRoleContext],
        work_items: tuple[AgentTeamWorkItem, ...],
        event: AgentTeamEvent,
        wakes: tuple[AgentTeamWakeRecord, ...],
    ) -> AgentTeamRecord:
        role_kinds = {"science", "safety", "completeness"}
        if (
            lifecycle_record.planning_mode != "multi_agent"
            or lifecycle_record.planning_team_id != team.team_id
            or lifecycle_record.planning_team_epoch != expected_team_epoch + 1
            or lifecycle_record.last_command_id != lifecycle_event.command_id
            or lifecycle_event.lifecycle_id != team.lifecycle_id
            or lifecycle_event.project_id != team.project_id
            or lifecycle_event.from_state != lifecycle_event.to_state
            or team.team_id == expected_team_id
            or team.team_epoch != expected_team_epoch + 1
            or team.status != "READY"
            or team.lease_owner is not None
            or team.snapshot_hash != context_snapshot.snapshot_hash
            or team.project_id != base_context.project_id
            or team.lifecycle_id != base_context.lifecycle_id
            or not base_context.complete
            or context_snapshot.team_id != team.team_id
            or context_snapshot.team_epoch != team.team_epoch
            or context_snapshot.project_id != team.project_id
            or context_snapshot.lifecycle_id != team.lifecycle_id
            or context_snapshot.base_context_hash != base_context.base_context_hash
            or context_snapshot.consent_epoch != team.consent_epoch
            or context_snapshot.consent_hash != team.consent_hash
            or context_snapshot.role_registry_hash != team.role_registry_hash
            or context_snapshot.context_projector_hash != team.context_projector_hash
            or set(role_contexts) != role_kinds
            or {item.reviewer_kind for item in work_items} != role_kinds
            or len(work_items) != 3
            or len(wakes) != 3
            or {wake.step_key for wake in wakes}
            != {f"work:{item.work_item_id}:attempt:1" for item in work_items}
            or event.team_id != team.team_id
            or event.team_epoch != team.team_epoch
            or event.project_id != team.project_id
            or event.lifecycle_id != team.lifecycle_id
            or event.command_id != lifecycle_event.command_id
            or event.event_type != "TEAM_REQUESTED"
            or any(
                wake.team_id != team.team_id
                or wake.team_epoch != team.team_epoch
                or wake.project_id != team.project_id
                or wake.lifecycle_id != team.lifecycle_id
                or wake.reason != "work_item"
                or wake.status != "PENDING"
                for wake in wakes
            )
        ):
            raise RuntimeError("AGENT_TEAM_BUNDLE_BINDING_INVALID")
        if (
            team.budget.max_model_calls > team.budget.max_repairs + 3
            or team.budget.max_model_calls > 6
        ):
            raise RuntimeError("AGENT_TEAM_BUDGET_INVALID")
        for item in work_items:
            role_context = role_contexts.get(item.reviewer_kind)
            if (
                item.team_id != team.team_id
                or item.team_epoch != team.team_epoch
                or item.project_id != team.project_id
                or item.lifecycle_id != team.lifecycle_id
                or item.attempt_no != 1
                or item.status != "PENDING"
                or role_context is None
                or role_context.team_id != team.team_id
                or role_context.team_epoch != team.team_epoch
                or role_context.project_id != team.project_id
                or role_context.lifecycle_id != team.lifecycle_id
                or role_context.reviewer_kind != item.reviewer_kind
                or role_context.role_id != item.role_id
                or role_context.base_context_hash != base_context.base_context_hash
                or role_context.model_profile_hash != team.model_profile_hash
                or role_context.consent_epoch != team.consent_epoch
                or role_context.consent_hash != team.consent_hash
                or role_context.role_context_hash
                != context_snapshot.role_context_hashes[item.reviewer_kind]
                or item.deadline_at > team.budget.deadline_at
            ):
                raise RuntimeError("AGENT_TEAM_BUNDLE_ROLE_BINDING_INVALID")

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior_event = conn.execute(
                """SELECT payload FROM agent_team_events
                   WHERE command_id=? AND event_type=?""",
                (event.command_id, event.event_type),
            ).fetchone()
            if prior_event is not None:
                prior = AgentTeamEvent.model_validate_json(prior_event["payload"])
                if (
                    prior.project_id != event.project_id
                    or prior.lifecycle_id != event.lifecycle_id
                    or prior.payload_hash != event.payload_hash
                    or prior.idempotency_key != event.idempotency_key
                ):
                    raise RuntimeError("AGENT_TEAM_COMMAND_REPLAYED")
                prior_team = conn.execute(
                    "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                    (prior.project_id, prior.team_id),
                ).fetchone()
                if prior_team is None:
                    raise RuntimeError("AGENT_TEAM_COMMAND_REPLAYED")
                return AgentTeamRecord.model_validate_json(prior_team["payload"])

            lifecycle_row = conn.execute(
                "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=?",
                (team.lifecycle_id, team.project_id),
            ).fetchone()
            if lifecycle_row is None:
                raise RuntimeError("LIFECYCLE_NOT_FOUND")
            current_lifecycle = _decode_agent_lifecycle(lifecycle_row["payload"])
            expected_lifecycle = current_lifecycle.model_copy(
                update={
                    "planning_mode": "multi_agent",
                    "planning_team_id": team.team_id,
                    "planning_team_epoch": expected_team_epoch + 1,
                    "updated_at": lifecycle_record.updated_at,
                    "last_command_id": lifecycle_event.command_id,
                }
            )
            if (
                current_lifecycle.planning_team_id != expected_team_id
                or current_lifecycle.planning_team_epoch != expected_team_epoch
                or current_lifecycle.state != lifecycle_event.from_state
                or lifecycle_event.to_state != current_lifecycle.state
                or lifecycle_record != expected_lifecycle
            ):
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            consent = self._current_project_multi_agent_consent(conn, team.project_id)
            if (
                consent is None
                or not consent.enabled
                or consent.consent_epoch != team.consent_epoch
                or consent.consent_hash != team.consent_hash
                or team.budget.max_model_calls > consent.max_model_calls
                or team.budget.reserved_input_tokens > consent.max_input_tokens
            ):
                raise RuntimeError("MULTI_AGENT_CONSENT_INVALIDATED")
            active_team = conn.execute(
                """SELECT team_id FROM agent_teams
                   WHERE lifecycle_id=? AND status NOT IN ('COMPLETED','NEEDS_ATTENTION','CANCELED','FAILED')
                   LIMIT 1""",
                (team.lifecycle_id,),
            ).fetchone()
            if active_team is not None:
                raise RuntimeError("AGENT_TEAM_ALREADY_ACTIVE")

            conn.execute(
                """INSERT OR IGNORE INTO agent_planning_base_contexts
                   (base_context_hash, project_id, lifecycle_id, planning_epoch, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    base_context.base_context_hash,
                    base_context.project_id,
                    base_context.lifecycle_id,
                    base_context.planning_epoch,
                    self._dump_model(base_context),
                    base_context.created_at.isoformat(),
                ),
            )
            stored_base = conn.execute(
                """SELECT payload FROM agent_planning_base_contexts
                   WHERE lifecycle_id=? AND planning_epoch=?""",
                (base_context.lifecycle_id, base_context.planning_epoch),
            ).fetchone()
            if (
                stored_base is None
                or AgentPlanningBaseContext.model_validate_json(stored_base["payload"])
                != base_context
            ):
                raise RuntimeError("AGENT_PLANNING_BASE_CONTEXT_CONFLICT")

            cursor = conn.execute(
                """UPDATE agent_lifecycles SET payload=?, updated_at=?
                   WHERE lifecycle_id=? AND project_id=? AND state=?
                     AND json_extract(payload, '$.planning_mode')=?
                     AND json_extract(payload, '$.planning_team_id') IS ?
                     AND json_extract(payload, '$.planning_team_epoch')=?""",
                (
                    self._dump_model(lifecycle_record),
                    lifecycle_record.updated_at.isoformat(),
                    lifecycle_record.lifecycle_id,
                    lifecycle_record.project_id,
                    current_lifecycle.state,
                    current_lifecycle.planning_mode,
                    expected_team_id,
                    expected_team_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            conn.execute(
                """INSERT INTO agent_teams
                   (team_id, team_epoch, project_id, lifecycle_id, status, payload, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    team.team_id,
                    team.team_epoch,
                    team.project_id,
                    team.lifecycle_id,
                    team.status,
                    self._dump_model(team),
                    team.created_at.isoformat(),
                    team.updated_at.isoformat(),
                ),
            )
            conn.execute(
                """INSERT INTO agent_team_contexts
                   (snapshot_hash, team_id, team_epoch, project_id, lifecycle_id,
                    base_context_hash, payload, role_contexts_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    context_snapshot.snapshot_hash,
                    team.team_id,
                    team.team_epoch,
                    team.project_id,
                    team.lifecycle_id,
                    context_snapshot.base_context_hash,
                    self._dump_model(context_snapshot),
                    json.dumps(
                        {
                            kind: role.model_dump(mode="json")
                            for kind, role in role_contexts.items()
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    context_snapshot.created_at.isoformat(),
                ),
            )
            for item in work_items:
                conn.execute(
                    """INSERT INTO agent_team_work_items
                       (work_item_id, team_id, team_epoch, project_id, lifecycle_id,
                        reviewer_kind, attempt_no, status, available_at, payload, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item.work_item_id,
                        item.team_id,
                        item.team_epoch,
                        item.project_id,
                        item.lifecycle_id,
                        item.reviewer_kind,
                        item.attempt_no,
                        item.status,
                        item.available_at.isoformat(),
                        self._dump_model(item),
                        item.created_at.isoformat(),
                        item.updated_at.isoformat(),
                    ),
                )
            self._insert_agent_team_event(conn, event)
            for wake in wakes:
                self._insert_agent_team_wake(conn, wake)
            self._insert_agent_lifecycle_event(conn, lifecycle_event)
        return team

    def get_agent_team(self, project_id: str, team_id: str) -> AgentTeamRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, team_id),
            ).fetchone()
        return AgentTeamRecord.model_validate_json(row["payload"]) if row else None

    def get_agent_team_creation_by_command(
        self, project_id: str, lifecycle_id: str, command_id: str
    ) -> tuple[AgentTeamRecord, AgentTeamEvent] | None:
        with self._lock, self._connect() as conn:
            event_row = conn.execute(
                """SELECT payload FROM agent_team_events
                   WHERE project_id=? AND lifecycle_id=? AND command_id=?
                     AND event_type='TEAM_REQUESTED'""",
                (project_id, lifecycle_id, command_id),
            ).fetchone()
            if event_row is None:
                return None
            event = AgentTeamEvent.model_validate_json(event_row["payload"])
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, event.team_id),
            ).fetchone()
        if team_row is None:
            raise SafetyError("AGENT_TEAM_COMMAND_RECORD_CORRUPT", code="AGENT_TEAM_COMMAND_RECORD_CORRUPT")
        return AgentTeamRecord.model_validate_json(team_row["payload"]), event

    def list_agent_teams(
        self, project_id: str, *, lifecycle_id: str | None = None
    ) -> list[AgentTeamRecord]:
        query = "SELECT payload FROM agent_teams WHERE project_id=?"
        params: tuple[object, ...] = (project_id,)
        if lifecycle_id is not None:
            query += " AND lifecycle_id=?"
            params = (*params, lifecycle_id)
        query += " ORDER BY team_epoch, team_id"
        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [AgentTeamRecord.model_validate_json(row["payload"]) for row in rows]

    def get_agent_team_context(
        self, project_id: str, team_id: str
    ) -> tuple[AgentTeamContextSnapshot, dict[AgentReviewerKind, AgentTeamRoleContext]] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT payload, role_contexts_json FROM agent_team_contexts
                   WHERE project_id=? AND team_id=?""",
                (project_id, team_id),
            ).fetchone()
        if row is None:
            return None
        snapshot = AgentTeamContextSnapshot.model_validate_json(row["payload"])
        raw_roles = json.loads(row["role_contexts_json"])
        roles = {
            kind: AgentTeamRoleContext.model_validate(value)
            for kind, value in raw_roles.items()
        }
        if set(roles) != set(snapshot.role_context_hashes) or any(
            roles[kind].role_context_hash != snapshot.role_context_hashes[kind]
            for kind in roles
        ):
            raise SafetyError("AGENT_TEAM_CONTEXT_PROJECTION_CORRUPT", code="AGENT_TEAM_CONTEXT_PROJECTION_CORRUPT")
        return snapshot, roles

    def get_agent_team_work_item(
        self, project_id: str, work_item_id: str
    ) -> AgentTeamWorkItem | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_team_work_items WHERE project_id=? AND work_item_id=?",
                (project_id, work_item_id),
            ).fetchone()
        return AgentTeamWorkItem.model_validate_json(row["payload"]) if row else None

    def list_agent_team_work_items(
        self, project_id: str, team_id: str
    ) -> list[AgentTeamWorkItem]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND team_id=? ORDER BY reviewer_kind, attempt_no""",
                (project_id, team_id),
            ).fetchall()
        return [AgentTeamWorkItem.model_validate_json(row["payload"]) for row in rows]

    def get_agent_team_model_call(
        self, project_id: str, model_call_id: str
    ) -> AgentTeamModelCallRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
                (project_id, model_call_id),
            ).fetchone()
        return AgentTeamModelCallRecord.model_validate_json(row["payload"]) if row else None

    def list_agent_team_model_calls(
        self, project_id: str, team_id: str
    ) -> list[AgentTeamModelCallRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM agent_team_model_calls
                   WHERE project_id=? AND team_id=? ORDER BY started_at, model_call_id""",
                (project_id, team_id),
            ).fetchall()
        return [AgentTeamModelCallRecord.model_validate_json(row["payload"]) for row in rows]

    def list_agent_team_findings(
        self, project_id: str, team_id: str
    ) -> list[AgentTeamFinding]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM agent_team_findings
                   WHERE project_id=? AND team_id=? ORDER BY created_at, finding_id""",
                (project_id, team_id),
            ).fetchall()
        return [AgentTeamFinding.model_validate_json(row["payload"]) for row in rows]

    def get_agent_team_advisory(
        self, project_id: str, team_id: str
    ) -> AgentTeamAdvisory | None:
        with self._lock, self._connect() as conn:
            team = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, team_id),
            ).fetchone()
            if team is None:
                return None
            record = AgentTeamRecord.model_validate_json(team["payload"])
            if record.advisory_id is None:
                return None
            row = conn.execute(
                """SELECT payload FROM agent_team_advisories
                   WHERE project_id=? AND team_id=? AND advisory_id=?""",
                (project_id, team_id, record.advisory_id),
            ).fetchone()
        return AgentTeamAdvisory.model_validate_json(row["payload"]) if row else None

    def list_agent_team_events(
        self, project_id: str, team_id: str
    ) -> list[AgentTeamEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM agent_team_events
                   WHERE project_id=? AND team_id=? ORDER BY occurred_at, event_id""",
                (project_id, team_id),
            ).fetchall()
        return [AgentTeamEvent.model_validate_json(row["payload"]) for row in rows]

    def list_agent_team_wakes(
        self, project_id: str, team_id: str
    ) -> list[AgentTeamWakeRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT payload FROM agent_team_wake_outbox
                   WHERE project_id=? AND team_id=? ORDER BY available_at, step_key""",
                (project_id, team_id),
            ).fetchall()
        return [AgentTeamWakeRecord.model_validate_json(row["payload"]) for row in rows]

    def enqueue_agent_team_wake(self, record: AgentTeamWakeRecord) -> AgentTeamWakeRecord:
        with self._lock, self._connect() as conn:
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (record.project_id, record.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            if (
                team is None
                or team.team_epoch != record.team_epoch
                or team.lifecycle_id != record.lifecycle_id
                or record.status != "PENDING"
            ):
                raise RuntimeError("AGENT_TEAM_WAKE_BINDING_INVALID")
            existing_row = conn.execute(
                "SELECT payload FROM agent_team_wake_outbox WHERE team_id=? AND step_key=?",
                (record.team_id, record.step_key),
            ).fetchone()
            if existing_row is not None:
                existing = AgentTeamWakeRecord.model_validate_json(existing_row["payload"])
                if (
                    existing.project_id != record.project_id
                    or existing.lifecycle_id != record.lifecycle_id
                    or existing.team_epoch != record.team_epoch
                    or existing.reason != record.reason
                ):
                    raise RuntimeError("AGENT_TEAM_WAKE_IDEMPOTENCY_CONFLICT")
                return existing
            self._insert_agent_team_wake(conn, record)
        return record

    def claim_next_agent_team_wake(
        self, *, owner: str, now: datetime, lease_expires_at: datetime
    ) -> AgentTeamWakeRecord | None:
        if not owner or lease_expires_at <= now:
            raise RuntimeError("AGENT_TEAM_LEASE_INVALID")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT wake_id, payload FROM agent_team_wake_outbox
                   WHERE (status IN ('PENDING','RETRY') AND available_at<=?)
                      OR (status='CLAIMED' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?)
                   ORDER BY available_at, created_at, wake_id LIMIT 1""",
                (now.isoformat(), now.isoformat()),
            ).fetchone()
            if row is None:
                return None
            wake = AgentTeamWakeRecord.model_validate_json(row["payload"])
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (wake.project_id, wake.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            if (
                team is None
                or team.team_epoch != wake.team_epoch
                or team.lifecycle_id != wake.lifecycle_id
                or not (
                    team.status in {"READY", "RUNNING", "AGGREGATING"}
                    or team.status == "COMPLETED" and wake.reason == "planning_resume"
                )
                or not self._lifecycle_has_current_team_binding(conn, team)
            ):
                canceled = wake.model_copy(update={
                    "status": "CANCELED",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                })
                conn.execute(
                    """UPDATE agent_team_wake_outbox SET status='CANCELED', lease_owner=NULL,
                       lease_expires_at=NULL, payload=?, updated_at=? WHERE wake_id=?""",
                    (self._dump_model(canceled), now.isoformat(), wake.wake_id),
                )
                return None
            if wake.status != "CLAIMED" and not wake_transition_allowed(wake.status, "CLAIMED"):
                raise RuntimeError("AGENT_TEAM_WAKE_TRANSITION_INVALID")
            claimed = AgentTeamWakeRecord.model_validate({
                **wake.model_dump(mode="python"),
                "status": "CLAIMED",
                "attempt_count": wake.attempt_count + 1,
                "lease_owner": owner,
                "lease_expires_at": lease_expires_at,
                "fencing_token": wake.fencing_token + 1,
                "updated_at": now,
            })
            cursor = conn.execute(
                """UPDATE agent_team_wake_outbox SET status=?, attempt_count=?, lease_owner=?,
                   lease_expires_at=?, payload=?, updated_at=? WHERE wake_id=?
                   AND json_extract(payload, '$.fencing_token')=?""",
                (
                    claimed.status, claimed.attempt_count, owner, lease_expires_at.isoformat(),
                    self._dump_model(claimed), now.isoformat(), wake.wake_id, wake.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return claimed

    def next_agent_team_wake_at(self) -> datetime | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """SELECT MIN(CASE WHEN status='CLAIMED' THEN lease_expires_at ELSE available_at END) AS due_at
                   FROM agent_team_wake_outbox WHERE status IN ('PENDING','RETRY')
                      OR (status='CLAIMED' AND lease_expires_at IS NOT NULL)"""
            ).fetchone()
        return datetime.fromisoformat(row["due_at"]) if row and row["due_at"] else None

    def complete_agent_team_wake(
        self, record: AgentTeamWakeRecord, *, owner: str, now: datetime
    ) -> AgentTeamWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TEAM_WAKE_OWNER_MISMATCH")
        if not wake_transition_allowed(record.status, "CONSUMED"):
            raise RuntimeError("AGENT_TEAM_WAKE_TRANSITION_INVALID")
        completed = AgentTeamWakeRecord.model_validate({
            **record.model_dump(mode="python"),
            "status": "CONSUMED",
            "lease_owner": None,
            "lease_expires_at": None,
            "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_team_wake_outbox SET status=?, lease_owner=NULL,
                   lease_expires_at=NULL, payload=?, updated_at=? WHERE wake_id=?
                   AND status='CLAIMED' AND lease_owner=?
                   AND json_extract(payload, '$.fencing_token')=?""",
                (
                    completed.status, self._dump_model(completed), now.isoformat(),
                    record.wake_id, owner, record.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_WAKE_CONCURRENT_UPDATE")
        return completed

    def retry_agent_team_wake(
        self,
        record: AgentTeamWakeRecord,
        *,
        owner: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
    ) -> AgentTeamWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TEAM_WAKE_OWNER_MISMATCH")
        if not wake_transition_allowed(record.status, "RETRY"):
            raise RuntimeError("AGENT_TEAM_WAKE_TRANSITION_INVALID")
        retried = AgentTeamWakeRecord.model_validate({
            **record.model_dump(mode="python"),
            "status": "RETRY",
            "available_at": available_at,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error_code": error_code,
            "updated_at": now,
        })
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_team_wake_outbox SET status=?, available_at=?, lease_owner=NULL,
                   lease_expires_at=NULL, payload=?, updated_at=? WHERE wake_id=?
                   AND status='CLAIMED' AND lease_owner=?
                   AND json_extract(payload, '$.fencing_token')=?""",
                (
                    retried.status, available_at.isoformat(), self._dump_model(retried),
                    now.isoformat(), record.wake_id, owner, record.fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_WAKE_CONCURRENT_UPDATE")
        return retried

    def fail_agent_team_wake(
        self,
        record: AgentTeamWakeRecord,
        *,
        owner: str,
        now: datetime,
        error_code: str,
    ) -> AgentTeamWakeRecord:
        if record.lease_owner != owner:
            raise RuntimeError("AGENT_TEAM_WAKE_OWNER_MISMATCH")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            wake_row = conn.execute(
                "SELECT payload FROM agent_team_wake_outbox WHERE wake_id=?",
                (record.wake_id,),
            ).fetchone()
            if wake_row is None:
                raise RuntimeError("AGENT_TEAM_WAKE_NOT_FOUND")
            current_wake = AgentTeamWakeRecord.model_validate_json(wake_row["payload"])
            if (
                current_wake.status != "CLAIMED"
                or current_wake.lease_owner != owner
                or current_wake.fencing_token != record.fencing_token
            ):
                raise RuntimeError("AGENT_TEAM_WAKE_CONCURRENT_UPDATE")
            canceled_wake = AgentTeamWakeRecord.model_validate({
                **current_wake.model_dump(mode="python"),
                "status": "CANCELED",
                "lease_owner": None,
                "lease_expires_at": None,
                "fencing_token": current_wake.fencing_token + 1,
                "last_error_code": error_code,
                "updated_at": now,
            })
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (record.project_id, record.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            if team is not None and team.status in {"READY", "RUNNING", "AGGREGATING"}:
                if not team_transition_allowed(team.status, "NEEDS_ATTENTION"):
                    raise RuntimeError("AGENT_TEAM_TRANSITION_INVALID")
                updated_team = team.model_copy(update={
                    "status": "NEEDS_ATTENTION",
                    "updated_at": now,
                })
                conn.execute(
                    "UPDATE agent_teams SET status=?, payload=?, updated_at=? WHERE project_id=? AND team_id=?",
                    (updated_team.status, self._dump_model(updated_team), now.isoformat(),
                     team.project_id, team.team_id),
                )
                work_rows = conn.execute(
                    "SELECT payload FROM agent_team_work_items WHERE project_id=? AND team_id=?",
                    (team.project_id, team.team_id),
                ).fetchall()
                for work_row in work_rows:
                    work = AgentTeamWorkItem.model_validate_json(work_row["payload"])
                    if work.status in {"COMPLETED", "FAILED", "INDETERMINATE", "CANCELED"}:
                        continue
                    target = "INDETERMINATE" if (
                        work.status == "MODEL_STARTED"
                        and work.model_call_id is not None
                        and self._agent_team_call_was_network_called(
                            conn, team.project_id, work.model_call_id
                        )
                    ) else "CANCELED"
                    updated_work = work.model_copy(update={
                        "status": target,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "fencing_token": work.fencing_token + 1,
                        "error_code": error_code,
                        "updated_at": now,
                    })
                    conn.execute(
                        "UPDATE agent_team_work_items SET status=?, payload=?, updated_at=? "
                        "WHERE project_id=? AND team_id=? AND work_item_id=?",
                        (updated_work.status, self._dump_model(updated_work), now.isoformat(),
                         team.project_id, team.team_id, work.work_item_id),
                    )
                    if work.status == "MODEL_STARTED" and work.model_call_id is not None:
                        call_row = conn.execute(
                            "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
                            (team.project_id, work.model_call_id),
                        ).fetchone()
                        if call_row is not None:
                            call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"])
                            if call.status == "started":
                                call_status = "indeterminate" if call.network_called else "canceled"
                                failed_call = call.model_copy(update={
                                    "status": call_status,
                                    "error_code": error_code,
                                    "completed_at": now,
                                })
                                conn.execute(
                                    "UPDATE agent_team_model_calls SET status=?, payload=? "
                                    "WHERE project_id=? AND model_call_id=? AND status='started'",
                                    (failed_call.status, self._dump_model(failed_call),
                                     team.project_id, call.model_call_id),
                                )
                conn.execute(
                    "UPDATE agent_team_wake_outbox SET status='CANCELED', lease_owner=NULL, "
                    "lease_expires_at=NULL, payload=json_set(payload, '$.status', 'CANCELED', "
                    "'$.lease_owner', NULL, '$.lease_expires_at', NULL, "
                    "'$.fencing_token', json_extract(payload, '$.fencing_token') + 1, "
                    "'$.last_error_code', ?, '$.updated_at', ?), updated_at=? "
                    "WHERE project_id=? AND team_id=? AND status IN ('PENDING','RETRY','CLAIMED') "
                    "AND wake_id<>?",
                    (error_code, now.isoformat(), now.isoformat(), team.project_id, team.team_id, record.wake_id),
                )
                event = AgentTeamEvent(
                    event_id=f"team_event_{uuid4().hex}",
                    team_id=team.team_id,
                    team_epoch=team.team_epoch,
                    project_id=team.project_id,
                    lifecycle_id=team.lifecycle_id,
                    command_id=f"scheduler-failed:{record.wake_id}",
                    idempotency_key=f"scheduler-failed:{record.wake_id}",
                    source="scheduler",
                    event_type="TEAM_NEEDS_ATTENTION",
                    payload_hash=canonical_hash({"wake_id": record.wake_id, "error_code": error_code}),
                    occurred_at=now,
                )
                self._insert_agent_team_event(conn, event)
            conn.execute(
                "UPDATE agent_team_wake_outbox SET status=?, lease_owner=NULL, lease_expires_at=NULL, "
                "payload=?, updated_at=? WHERE wake_id=? AND status='CLAIMED' AND lease_owner=? "
                "AND json_extract(payload, '$.fencing_token')=?",
                (canceled_wake.status, self._dump_model(canceled_wake), now.isoformat(),
                 record.wake_id, owner, record.fencing_token),
            )
        return canceled_wake

    @staticmethod
    def _agent_team_call_was_network_called(
        conn: sqlite3.Connection, project_id: str, model_call_id: str
    ) -> bool:
        row = conn.execute(
            "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
            (project_id, model_call_id),
        ).fetchone()
        if row is None:
            return False
        return AgentTeamModelCallRecord.model_validate_json(row["payload"]).network_called

    @staticmethod
    def _stop_agent_team_children(
        conn: sqlite3.Connection,
        *,
        team: AgentTeamRecord,
        now: datetime,
        error_code: str,
    ) -> None:
        work_rows = conn.execute(
            "SELECT payload FROM agent_team_work_items WHERE project_id=? AND team_id=?",
            (team.project_id, team.team_id),
        ).fetchall()
        for row in work_rows:
            work = AgentTeamWorkItem.model_validate_json(row["payload"])
            if work.status in {"COMPLETED", "FAILED", "INDETERMINATE", "CANCELED"}:
                continue
            call = None
            if work.status == "MODEL_STARTED" and work.model_call_id is not None:
                call_row = conn.execute(
                    "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
                    (team.project_id, work.model_call_id),
                ).fetchone()
                call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"]) if call_row else None
            target = "INDETERMINATE" if call is not None and call.network_called else "CANCELED"
            stopped_work = AgentTeamWorkItem.model_validate({
                **work.model_dump(mode="python"),
                "status": target,
                "lease_owner": None,
                "lease_expires_at": None,
                "fencing_token": work.fencing_token + 1,
                "error_code": error_code,
                "updated_at": now,
            })
            conn.execute(
                "UPDATE agent_team_work_items SET status=?, payload=?, updated_at=? "
                "WHERE project_id=? AND team_id=? AND work_item_id=? AND status=? "
                "AND json_extract(payload, '$.fencing_token')=?",
                (stopped_work.status, SQLiteDesktopStore._dump_model(stopped_work), now.isoformat(),
                 team.project_id, team.team_id, work.work_item_id, work.status, work.fencing_token),
            )
            if call is not None and call.status == "started":
                call_status = "indeterminate" if call.network_called else "canceled"
                stopped_call = AgentTeamModelCallRecord.model_validate({
                    **call.model_dump(mode="python"),
                    "status": call_status,
                    "error_code": error_code,
                    "completed_at": now,
                })
                conn.execute(
                    "UPDATE agent_team_model_calls SET status=?, payload=? "
                    "WHERE project_id=? AND model_call_id=? AND status='started'",
                    (stopped_call.status, SQLiteDesktopStore._dump_model(stopped_call),
                     team.project_id, call.model_call_id),
                )
        wake_rows = conn.execute(
            "SELECT payload FROM agent_team_wake_outbox WHERE project_id=? AND team_id=? "
            "AND status IN ('PENDING','RETRY','CLAIMED')",
            (team.project_id, team.team_id),
        ).fetchall()
        for row in wake_rows:
            wake = AgentTeamWakeRecord.model_validate_json(row["payload"])
            if not wake_transition_allowed(wake.status, "CANCELED"):
                continue
            stopped_wake = AgentTeamWakeRecord.model_validate({
                **wake.model_dump(mode="python"),
                "status": "CANCELED",
                "lease_owner": None,
                "lease_expires_at": None,
                "fencing_token": wake.fencing_token + 1,
                "last_error_code": error_code,
                "updated_at": now,
            })
            conn.execute(
                "UPDATE agent_team_wake_outbox SET status=?, lease_owner=NULL, lease_expires_at=NULL, "
                "payload=?, updated_at=? WHERE wake_id=? AND status=? "
                "AND json_extract(payload, '$.fencing_token')=?",
                (stopped_wake.status, SQLiteDesktopStore._dump_model(stopped_wake), now.isoformat(),
                 wake.wake_id, wake.status, wake.fencing_token),
            )

    def claim_agent_team_work_and_start_call(
        self,
        *,
        work_item_id: str,
        expected_fencing_token: int,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        call: AgentTeamModelCallRecord,
        event: AgentTeamEvent,
    ) -> tuple[AgentTeamWorkItem, AgentTeamModelCallRecord]:
        if (
            not owner
            or lease_expires_at <= now
            or call.status != "started"
            or call.network_called
            or event.event_type != "MODEL_CALL_STARTED"
            or event.team_id != call.team_id
            or event.team_epoch != call.team_epoch
            or event.project_id != call.project_id
            or event.lifecycle_id != call.lifecycle_id
        ):
            raise RuntimeError("AGENT_TEAM_MODEL_CALL_START_INVALID")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND team_id=? AND work_item_id=?""",
                (call.project_id, call.team_id, work_item_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_NOT_FOUND")
            work = AgentTeamWorkItem.model_validate_json(row["payload"])
            if (
                work.team_epoch != call.team_epoch
                or work.lifecycle_id != call.lifecycle_id
                or work.work_item_id != call.work_item_id
                or work.reviewer_kind != call.reviewer_kind
                or work.role_id != call.role_id
                or work.attempt_no != call.attempt_no
                or work.fencing_token != expected_fencing_token
                or work.status not in {"PENDING", "CLAIMED", "RETRY_READY"}
                or work.available_at > now
                or work.deadline_at <= now
                or (
                    work.status == "RETRY_READY"
                    and (
                        call.attempt_no != 2
                        or work.model_call_id != call.repair_of_model_call_id
                    )
                )
                or (
                    work.status != "RETRY_READY"
                    and (work.model_call_id is not None or call.attempt_no != 1)
                )
            ):
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_CLAIM_INVALIDATED")
            if work.status == "CLAIMED" and (
                work.lease_expires_at is None or work.lease_expires_at > now
            ):
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_ALREADY_CLAIMED")
            for from_status, to_status in (
                (work.status, "CLAIMED"),
                ("CLAIMED", "MODEL_STARTED"),
            ):
                if from_status != to_status and not work_item_transition_allowed(
                    from_status, to_status
                ):
                    raise RuntimeError("AGENT_TEAM_WORK_ITEM_TRANSITION_INVALID")

            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (call.project_id, call.team_id),
            ).fetchone()
            if team_row is None:
                raise RuntimeError("AGENT_TEAM_NOT_FOUND")
            team = AgentTeamRecord.model_validate_json(team_row["payload"])
            consent = self._current_project_multi_agent_consent(conn, call.project_id)
            if (
                team.team_epoch != call.team_epoch
                or team.lifecycle_id != call.lifecycle_id
                or team.status not in {"READY", "RUNNING"}
                or not self._lifecycle_has_current_team_binding(conn, team)
                or consent is None
                or not consent.enabled
                or consent.consent_epoch != call.consent_epoch
                or consent.consent_hash != call.consent_hash
                or team.consent_epoch != call.consent_epoch
                or team.consent_hash != call.consent_hash
                or call.role_registry_hash != team.role_registry_hash
                or call.model_profile_hash != team.model_profile_hash
                or call.attempt_no == 1 and call.repair_of_model_call_id is not None
                or call.attempt_no == 2 and call.repair_of_model_call_id is None
            ):
                raise RuntimeError("MULTI_AGENT_CONSENT_INVALIDATED")
            context_row = conn.execute(
                """SELECT role_contexts_json FROM agent_team_contexts
                   WHERE project_id=? AND team_id=? AND snapshot_hash=?""",
                (call.project_id, call.team_id, team.snapshot_hash),
            ).fetchone()
            if context_row is None:
                raise RuntimeError("AGENT_TEAM_CONTEXT_NOT_FOUND")
            role_payload = json.loads(context_row["role_contexts_json"]).get(
                call.reviewer_kind
            )
            role_context = (
                AgentTeamRoleContext.model_validate(role_payload)
                if isinstance(role_payload, dict)
                else None
            )
            if (
                role_context is None
                or role_context.role_context_hash != call.role_context_hash
                or role_context.role_id != call.role_id
                or role_context.model_profile_hash != call.model_profile_hash
                or role_context.prompt_template_version != call.prompt_template_version
                or role_context.output_schema_version != call.output_schema_version
                or role_context.consent_epoch != call.consent_epoch
                or role_context.consent_hash != call.consent_hash
            ):
                raise RuntimeError("AGENT_TEAM_ROLE_CONTEXT_INVALIDATED")
            prior_calls = conn.execute(
                "SELECT COUNT(*) FROM agent_team_model_calls WHERE team_id=?",
                (call.team_id,),
            ).fetchone()[0]
            if prior_calls >= team.budget.max_model_calls:
                raise RuntimeError("MULTI_AGENT_BUDGET_EXCEEDED")

            updated_work = work.model_copy(
                update={
                    "status": "MODEL_STARTED",
                    "model_call_id": call.model_call_id,
                    "request_hash": call.request_hash,
                    "lease_owner": owner,
                    "lease_expires_at": lease_expires_at,
                    "fencing_token": expected_fencing_token + 1,
                    "updated_at": now,
                }
            )
            if team.status == "READY":
                if not team_transition_allowed(team.status, "RUNNING"):
                    raise RuntimeError("AGENT_TEAM_TRANSITION_INVALID")
                updated_team = team.model_copy(update={"status": "RUNNING", "updated_at": now})
            else:
                updated_team = team
            cursor = conn.execute(
                """UPDATE agent_team_work_items SET status=?, available_at=?, payload=?, updated_at=?
                   WHERE project_id=? AND team_id=? AND work_item_id=? AND status=?
                     AND json_extract(payload, '$.fencing_token')=?""",
                (
                    updated_work.status,
                    updated_work.available_at.isoformat(),
                    self._dump_model(updated_work),
                    updated_work.updated_at.isoformat(),
                    work.project_id,
                    work.team_id,
                    work.work_item_id,
                    work.status,
                    expected_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_CLAIM_INVALIDATED")
            conn.execute(
                """INSERT INTO agent_team_model_calls
                   (model_call_id, team_id, team_epoch, project_id, lifecycle_id,
                    work_item_id, request_hash, attempt_no, status, started_at, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    call.model_call_id,
                    call.team_id,
                    call.team_epoch,
                    call.project_id,
                    call.lifecycle_id,
                    call.work_item_id,
                    call.request_hash,
                    call.attempt_no,
                    call.status,
                    call.started_at.isoformat(),
                    self._dump_model(call),
                ),
            )
            if updated_team != team:
                conn.execute(
                    """UPDATE agent_teams SET status=?, payload=?, updated_at=?
                       WHERE project_id=? AND team_id=? AND status='READY'""",
                    (
                        updated_team.status,
                        self._dump_model(updated_team),
                        updated_team.updated_at.isoformat(),
                        team.project_id,
                        team.team_id,
                    ),
                )
            self._insert_agent_team_event(conn, event)
        return updated_work, call

    def reclaim_agent_team_call(
        self,
        *,
        project_id: str,
        model_call_id: str,
        expected_fencing_token: int,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        event: AgentTeamEvent,
    ) -> tuple[AgentTeamWorkItem, AgentTeamModelCallRecord]:
        if not owner or lease_expires_at <= now:
            raise RuntimeError("AGENT_TEAM_LEASE_INVALID")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            call_row = conn.execute(
                """SELECT payload FROM agent_team_model_calls
                   WHERE project_id=? AND model_call_id=?""",
                (project_id, model_call_id),
            ).fetchone()
            if call_row is None:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_FOUND")
            call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"])
            if call.status != "started":
                raise RuntimeError("AGENT_TEAM_CALL_RECLAIM_INVALID")
            work_row = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND team_id=? AND work_item_id=?""",
                (project_id, call.team_id, call.work_item_id),
            ).fetchone()
            if work_row is None:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_NOT_FOUND")
            work = AgentTeamWorkItem.model_validate_json(work_row["payload"])
            if (
                work.status != "MODEL_STARTED"
                or work.model_call_id != model_call_id
                or work.fencing_token != expected_fencing_token
                or work.lease_expires_at is None
                or work.lease_expires_at > now
            ):
                raise RuntimeError("AGENT_TEAM_FENCING_TOKEN_STALE")
            if (
                event.team_id != call.team_id
                or event.team_epoch != call.team_epoch
                or event.project_id != project_id
                or event.lifecycle_id != call.lifecycle_id
                or event.event_type != "WORK_ITEM_CLAIMED"
            ):
                raise RuntimeError("AGENT_TEAM_CALL_RECLAIM_INVALID")
            updated_work = work.model_copy(
                update={
                    "lease_owner": owner,
                    "lease_expires_at": lease_expires_at,
                    "fencing_token": expected_fencing_token + 1,
                    "updated_at": now,
                }
            )
            cursor = conn.execute(
                """UPDATE agent_team_work_items SET payload=?, updated_at=?
                   WHERE project_id=? AND team_id=? AND work_item_id=?
                     AND status='MODEL_STARTED'
                     AND json_extract(payload, '$.fencing_token')=?""",
                (
                    self._dump_model(updated_work),
                    now.isoformat(),
                    project_id,
                    work.team_id,
                    work.work_item_id,
                    expected_fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_FENCING_TOKEN_STALE")
            self._insert_agent_team_event(conn, event)
        return updated_work, call

    def mark_agent_team_call_network_called(
        self,
        *,
        project_id: str,
        model_call_id: str,
        owner: str,
        fencing_token: int,
        now: datetime,
    ) -> AgentTeamModelCallRecord:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            call_row = conn.execute(
                """SELECT payload FROM agent_team_model_calls
                   WHERE project_id=? AND model_call_id=?""",
                (project_id, model_call_id),
            ).fetchone()
            if call_row is None:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_FOUND")
            call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"])
            if call.status != "started":
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_STARTED")
            work_row = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND work_item_id=? AND team_id=?""",
                (project_id, call.work_item_id, call.team_id),
            ).fetchone()
            if work_row is None:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_NOT_FOUND")
            work = AgentTeamWorkItem.model_validate_json(work_row["payload"])
            if (
                work.status != "MODEL_STARTED"
                or work.model_call_id != call.model_call_id
                or work.lease_owner != owner
                or work.fencing_token != fencing_token
                or work.lease_expires_at is None
                or work.lease_expires_at <= now
            ):
                raise RuntimeError("AGENT_TEAM_FENCING_TOKEN_STALE")
            if call.network_called:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NETWORK_ALREADY_STARTED")
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, call.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            consent = self._current_project_multi_agent_consent(conn, project_id)
            current_binding = team is not None and self._lifecycle_has_current_team_binding(conn, team)
            current_consent = (
                consent is not None
                and consent.enabled
                and consent.consent_epoch == call.consent_epoch
                and consent.consent_hash == call.consent_hash
            )
            if (
                current_binding
                and current_consent
                and team is not None
                and team.status in {"READY", "RUNNING"}
            ):
                updated_call = call.model_copy(update={"network_called": True})
                cursor = conn.execute(
                    """UPDATE agent_team_model_calls SET payload=?
                       WHERE project_id=? AND model_call_id=? AND status='started'
                         AND json_extract(payload, '$.network_called')=0""",
                    (self._dump_model(updated_call), project_id, model_call_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("AGENT_TEAM_MODEL_CALL_PRENETWORK_CAS_FAILED")
                return updated_call

            updated_call = call.model_copy(
                update={
                    "status": "canceled",
                    "completed_at": now,
                    "error_code": "TEAM_BINDING_INVALIDATED",
                }
            )
            updated_work = work.model_copy(
                update={
                    "status": "CANCELED",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "error_code": "TEAM_BINDING_INVALIDATED",
                    "updated_at": now,
                }
            )
            conn.execute(
                """UPDATE agent_team_model_calls SET status=?, payload=?
                   WHERE project_id=? AND model_call_id=? AND status='started'
                     AND json_extract(payload, '$.network_called')=0""",
                (updated_call.status, self._dump_model(updated_call), project_id, model_call_id),
            )
            conn.execute(
                """UPDATE agent_team_work_items SET status=?, payload=?, updated_at=?
                   WHERE project_id=? AND work_item_id=? AND status='MODEL_STARTED'
                     AND json_extract(payload, '$.lease_owner')=?
                     AND json_extract(payload, '$.fencing_token')=?""",
                (
                    updated_work.status,
                    self._dump_model(updated_work),
                    now.isoformat(),
                    project_id,
                    work.work_item_id,
                    owner,
                    fencing_token,
                ),
            )
            if team is not None and team.status in {"READY", "RUNNING", "AGGREGATING"}:
                target_status = "NEEDS_ATTENTION" if current_binding else "CANCELED"
                if team_transition_allowed(team.status, target_status):
                    updated_team = team.model_copy(
                        update={"status": target_status, "updated_at": now}
                    )
                    conn.execute(
                        """UPDATE agent_teams SET status=?, payload=?, updated_at=?
                           WHERE project_id=? AND team_id=? AND status=?""",
                        (
                            updated_team.status,
                            self._dump_model(updated_team),
                            now.isoformat(),
                            project_id,
                            team.team_id,
                            team.status,
                        ),
                    )
            invalidation_event = AgentTeamEvent(
                event_id=f"team_event_{uuid4().hex}",
                team_id=call.team_id,
                team_epoch=call.team_epoch,
                project_id=project_id,
                lifecycle_id=call.lifecycle_id,
                command_id=f"preflight:{call.model_call_id}",
                idempotency_key=f"preflight:{call.model_call_id}",
                source="system",
                event_type="TEAM_BINDING_INVALIDATED",
                payload_hash=canonical_hash(
                    {"model_call_id": call.model_call_id, "network_called": False}
                ),
                occurred_at=now,
            )
            self._insert_agent_team_event(conn, invalidation_event)
            if team is not None:
                self._stop_agent_team_children(
                    conn,
                    team=team,
                    now=now,
                    error_code="TEAM_BINDING_INVALIDATED",
                )
            return updated_call

    def complete_agent_team_model_call(
        self,
        *,
        project_id: str,
        model_call_id: str,
        owner: str,
        fencing_token: int,
        now: datetime,
        completed_call: AgentTeamModelCallRecord,
        findings: tuple[AgentTeamFinding, ...],
        event: AgentTeamEvent,
        wake: AgentTeamWakeRecord,
    ) -> AgentTeamModelCallRecord:
        if (
            completed_call.model_call_id != model_call_id
            or completed_call.project_id != project_id
            or completed_call.status != "completed"
            or not completed_call.network_called
            or completed_call.response_hash is None
            or completed_call.schema_valid is not True
            or completed_call.completed_at is None
            or event.team_id != completed_call.team_id
            or event.team_epoch != completed_call.team_epoch
            or event.project_id != project_id
            or event.lifecycle_id != completed_call.lifecycle_id
            or event.event_type != "MODEL_CALL_COMPLETED"
            or wake.team_id != completed_call.team_id
            or wake.team_epoch != completed_call.team_epoch
            or wake.project_id != project_id
            or wake.lifecycle_id != completed_call.lifecycle_id
            or wake.status != "PENDING"
        ):
            raise RuntimeError("AGENT_TEAM_MODEL_CALL_COMPLETION_INVALID")

        immutable_call_fields = (
            "model_call_id",
            "team_id",
            "team_epoch",
            "project_id",
            "lifecycle_id",
            "work_item_id",
            "reviewer_kind",
            "role_id",
            "role_registry_hash",
            "role_context_hash",
            "model_profile_hash",
            "request_hash",
            "request_bytes",
            "provider_id",
            "model_id",
            "endpoint_class",
            "prompt_template_version",
            "output_schema_version",
            "consent_epoch",
            "consent_hash",
            "attempt_no",
            "repair_of_model_call_id",
            "started_at",
        )
        for finding in findings:
            if (
                finding.team_id != completed_call.team_id
                or finding.team_epoch != completed_call.team_epoch
                or finding.project_id != project_id
                or finding.lifecycle_id != completed_call.lifecycle_id
                or finding.work_item_id != completed_call.work_item_id
                or finding.reviewer_kind != completed_call.reviewer_kind
                or finding.role_id != completed_call.role_id
                or finding.role_context_hash != completed_call.role_context_hash
                or finding.model_call_id != model_call_id
                or finding.model_response_hash != completed_call.response_hash
            ):
                raise RuntimeError("AGENT_TEAM_FINDING_BINDING_INVALID")

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            call_row = conn.execute(
                """SELECT payload FROM agent_team_model_calls
                   WHERE project_id=? AND model_call_id=?""",
                (project_id, model_call_id),
            ).fetchone()
            if call_row is None:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_FOUND")
            current_call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"])
            if current_call.status == "completed":
                prior_event = conn.execute(
                    """SELECT payload FROM agent_team_events
                       WHERE team_id=? AND idempotency_key=?""",
                    (current_call.team_id, event.idempotency_key),
                ).fetchone()
                prior_wake = conn.execute(
                    """SELECT payload FROM agent_team_wake_outbox
                       WHERE team_id=? AND step_key=?""",
                    (current_call.team_id, wake.step_key),
                ).fetchone()
                prior_findings = conn.execute(
                    """SELECT payload FROM agent_team_findings
                       WHERE project_id=?
                         AND json_extract(payload, '$.model_call_id')=?
                       ORDER BY finding_id""",
                    (project_id, model_call_id),
                ).fetchall()
                if (
                    current_call == completed_call
                    and prior_event is not None
                    and AgentTeamEvent.model_validate_json(prior_event["payload"]) == event
                    and prior_wake is not None
                    and AgentTeamWakeRecord.model_validate_json(prior_wake["payload"]) == wake
                    and tuple(
                        AgentTeamFinding.model_validate_json(row["payload"])
                        for row in prior_findings
                    )
                    == tuple(sorted(findings, key=lambda item: item.finding_id))
                ):
                    return current_call
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_COMPLETION_REPLAYED")
            if current_call.status != "started" or not current_call.network_called:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_COMPLETABLE")
            if any(
                getattr(current_call, name) != getattr(completed_call, name)
                for name in immutable_call_fields
            ):
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_IDENTITY_CHANGED")

            work_row = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND team_id=? AND work_item_id=?""",
                (project_id, current_call.team_id, current_call.work_item_id),
            ).fetchone()
            if work_row is None:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_NOT_FOUND")
            work = AgentTeamWorkItem.model_validate_json(work_row["payload"])
            if (
                work.status != "MODEL_STARTED"
                or work.model_call_id != model_call_id
                or work.lease_owner != owner
                or work.fencing_token != fencing_token
                or work.lease_expires_at is None
                or work.lease_expires_at <= now
            ):
                raise RuntimeError("AGENT_TEAM_FENCING_TOKEN_STALE")

            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, current_call.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            consent = self._current_project_multi_agent_consent(conn, project_id)
            current_binding = team is not None and self._lifecycle_has_current_team_binding(conn, team)
            current_consent = (
                consent is not None
                and consent.enabled
                and consent.consent_epoch == current_call.consent_epoch
                and consent.consent_hash == current_call.consent_hash
            )
            context_row = (
                conn.execute(
                    """SELECT role_contexts_json FROM agent_team_contexts
                       WHERE project_id=? AND team_id=? AND team_epoch=?""",
                    (project_id, current_call.team_id, current_call.team_epoch),
                ).fetchone()
                if team is not None
                else None
            )
            role_payload = (
                json.loads(context_row["role_contexts_json"]).get(current_call.reviewer_kind)
                if context_row is not None
                else None
            )
            role_context = (
                AgentTeamRoleContext.model_validate(role_payload)
                if isinstance(role_payload, dict)
                else None
            )
            context_valid = (
                role_context is not None
                and role_context.role_context_hash == current_call.role_context_hash
                and role_context.model_profile_hash == current_call.model_profile_hash
                and role_context.consent_epoch == current_call.consent_epoch
                and role_context.consent_hash == current_call.consent_hash
            )
            if not (
                team is not None
                and team.status == "RUNNING"
                and current_binding
                and current_consent
                and context_valid
            ):
                canceled_call = current_call.model_copy(
                    update={
                        "status": "canceled",
                        "completed_at": now,
                        "error_code": "TEAM_BINDING_INVALIDATED",
                    }
                )
                canceled_work = work.model_copy(
                    update={
                        "status": "CANCELED",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": "TEAM_BINDING_INVALIDATED",
                        "updated_at": now,
                    }
                )
                conn.execute(
                    """UPDATE agent_team_model_calls SET status=?, payload=?
                       WHERE project_id=? AND model_call_id=? AND status='started'""",
                    (canceled_call.status, self._dump_model(canceled_call), project_id, model_call_id),
                )
                conn.execute(
                    """UPDATE agent_team_work_items SET status=?, payload=?, updated_at=?
                       WHERE project_id=? AND team_id=? AND work_item_id=? AND status='MODEL_STARTED'
                         AND json_extract(payload, '$.lease_owner')=?
                         AND json_extract(payload, '$.fencing_token')=?""",
                    (
                        canceled_work.status,
                        self._dump_model(canceled_work),
                        now.isoformat(),
                        project_id,
                        work.team_id,
                        work.work_item_id,
                        owner,
                        fencing_token,
                    ),
                )
                if team is not None and team.status in {"READY", "RUNNING", "AGGREGATING"}:
                    target = "NEEDS_ATTENTION" if current_binding else "CANCELED"
                    if team_transition_allowed(team.status, target):
                        updated_team = team.model_copy(update={"status": target, "updated_at": now})
                        conn.execute(
                            """UPDATE agent_teams SET status=?, payload=?, updated_at=?
                               WHERE project_id=? AND team_id=? AND status=?""",
                            (
                                updated_team.status,
                                self._dump_model(updated_team),
                                now.isoformat(),
                                project_id,
                                team.team_id,
                                team.status,
                            ),
                        )
                invalidation_event = AgentTeamEvent(
                    event_id=f"team_event_{uuid4().hex}",
                    team_id=current_call.team_id,
                    team_epoch=current_call.team_epoch,
                    project_id=project_id,
                    lifecycle_id=current_call.lifecycle_id,
                    command_id=f"completion-invalidated:{model_call_id}",
                    idempotency_key=f"completion-invalidated:{model_call_id}",
                    source="system",
                    event_type="TEAM_BINDING_INVALIDATED",
                    payload_hash=canonical_hash({"model_call_id": model_call_id}),
                    occurred_at=now,
                )
                self._insert_agent_team_event(conn, invalidation_event)
                if team is not None:
                    self._stop_agent_team_children(
                        conn,
                        team=team,
                        now=now,
                        error_code="TEAM_BINDING_INVALIDATED",
                    )
                return canceled_call

            updated_work = work.model_copy(
                update={
                    "status": "COMPLETED",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            )
            if not (
                work_item_transition_allowed(work.status, "VALIDATING")
                and work_item_transition_allowed("VALIDATING", "COMPLETED")
            ):
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_TRANSITION_INVALID")
            cursor = conn.execute(
                """UPDATE agent_team_model_calls SET status=?, payload=?
                   WHERE project_id=? AND model_call_id=? AND status='started'
                     AND json_extract(payload, '$.network_called')=1""",
                (completed_call.status, self._dump_model(completed_call), project_id, model_call_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_COMPLETION_CAS_FAILED")
            cursor = conn.execute(
                """UPDATE agent_team_work_items SET status=?, payload=?, updated_at=?
                   WHERE project_id=? AND team_id=? AND work_item_id=? AND status='MODEL_STARTED'
                     AND json_extract(payload, '$.lease_owner')=?
                     AND json_extract(payload, '$.fencing_token')=?""",
                (
                    updated_work.status,
                    self._dump_model(updated_work),
                    now.isoformat(),
                    project_id,
                    work.team_id,
                    work.work_item_id,
                    owner,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_COMPLETION_CAS_FAILED")
            for finding in findings:
                conn.execute(
                    """INSERT INTO agent_team_findings
                       (finding_id, finding_hash, team_id, team_epoch, project_id,
                        lifecycle_id, reviewer_kind, severity, payload, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        finding.finding_id,
                        finding.finding_hash,
                        finding.team_id,
                        finding.team_epoch,
                        finding.project_id,
                        finding.lifecycle_id,
                        finding.reviewer_kind,
                        finding.severity,
                        self._dump_model(finding),
                        finding.created_at.isoformat(),
                    ),
                )
            self._insert_agent_team_event(conn, event)
            self._insert_agent_team_wake(conn, wake)
        return completed_call

    def fail_agent_team_model_call(
        self,
        *,
        project_id: str,
        model_call_id: str,
        owner: str,
        fencing_token: int,
        now: datetime,
        failed_call: AgentTeamModelCallRecord,
        event: AgentTeamEvent,
        retry_wake: AgentTeamWakeRecord | None = None,
    ) -> AgentTeamModelCallRecord:
        if (
            failed_call.model_call_id != model_call_id
            or failed_call.project_id != project_id
            or failed_call.status not in {"failed", "indeterminate"}
            or failed_call.completed_at is None
            or failed_call.error_code is None
            or event.team_id != failed_call.team_id
            or event.team_epoch != failed_call.team_epoch
            or event.project_id != project_id
            or event.lifecycle_id != failed_call.lifecycle_id
            or event.event_type != (
                "MODEL_CALL_INDETERMINATE"
                if failed_call.status == "indeterminate"
                else "MODEL_CALL_FAILED"
            )
            or retry_wake is not None
            and (
                failed_call.status != "failed"
                or failed_call.attempt_no != 1
                or not failed_call.network_called
                or failed_call.schema_valid is not False
                or retry_wake.team_id != failed_call.team_id
                or retry_wake.team_epoch != failed_call.team_epoch
                or retry_wake.project_id != project_id
                or retry_wake.lifecycle_id != failed_call.lifecycle_id
                or retry_wake.status != "PENDING"
                or retry_wake.reason != "work_item"
            )
        ):
            raise RuntimeError("AGENT_TEAM_MODEL_CALL_FAILURE_INVALID")

        immutable_fields = (
            "model_call_id", "team_id", "team_epoch", "project_id", "lifecycle_id",
            "work_item_id", "reviewer_kind", "role_id", "role_registry_hash",
            "role_context_hash", "model_profile_hash", "request_hash", "request_bytes",
            "provider_id", "model_id", "endpoint_class", "prompt_template_version",
            "output_schema_version", "consent_epoch", "consent_hash", "attempt_no",
            "repair_of_model_call_id", "started_at",
        )
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            call_row = conn.execute(
                "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
                (project_id, model_call_id),
            ).fetchone()
            if call_row is None:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_NOT_FOUND")
            current_call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"])
            prior_event = conn.execute(
                "SELECT payload FROM agent_team_events WHERE team_id=? AND idempotency_key=?",
                (current_call.team_id, event.idempotency_key),
            ).fetchone()
            prior_wake = (
                conn.execute(
                    "SELECT payload FROM agent_team_wake_outbox WHERE team_id=? AND step_key=?",
                    (current_call.team_id, retry_wake.step_key),
                ).fetchone()
                if retry_wake is not None
                else None
            )
            if current_call.status in {"failed", "indeterminate"}:
                if (
                    current_call == failed_call
                    and prior_event is not None
                    and AgentTeamEvent.model_validate_json(prior_event["payload"]) == event
                    and (
                        retry_wake is None
                        or prior_wake is not None
                        and AgentTeamWakeRecord.model_validate_json(prior_wake["payload"])
                        == retry_wake
                    )
                ):
                    return current_call
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_FAILURE_REPLAYED")
            if current_call.status != "started" or any(
                getattr(current_call, name) != getattr(failed_call, name)
                for name in immutable_fields
            ):
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_IDENTITY_CHANGED")

            work_row = conn.execute(
                """SELECT payload FROM agent_team_work_items
                   WHERE project_id=? AND team_id=? AND work_item_id=?""",
                (project_id, current_call.team_id, current_call.work_item_id),
            ).fetchone()
            if work_row is None:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_NOT_FOUND")
            work = AgentTeamWorkItem.model_validate_json(work_row["payload"])
            if (
                work.status != "MODEL_STARTED"
                or work.model_call_id != model_call_id
                or work.lease_owner != owner
                or work.fencing_token != fencing_token
                or work.lease_expires_at is None
                or work.lease_expires_at <= now
            ):
                raise RuntimeError("AGENT_TEAM_FENCING_TOKEN_STALE")

            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (project_id, current_call.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            consent = self._current_project_multi_agent_consent(conn, project_id)
            current_binding = team is not None and self._lifecycle_has_current_team_binding(conn, team)
            current_consent = (
                consent is not None
                and consent.enabled
                and consent.consent_epoch == current_call.consent_epoch
                and consent.consent_hash == current_call.consent_hash
            )
            context_row = (
                conn.execute(
                    """SELECT role_contexts_json FROM agent_team_contexts
                       WHERE project_id=? AND team_id=? AND team_epoch=?""",
                    (project_id, current_call.team_id, current_call.team_epoch),
                ).fetchone()
                if team is not None
                else None
            )
            role_payload = (
                json.loads(context_row["role_contexts_json"]).get(current_call.reviewer_kind)
                if context_row is not None
                else None
            )
            role_context = (
                AgentTeamRoleContext.model_validate(role_payload)
                if isinstance(role_payload, dict)
                else None
            )
            if not (
                team is not None
                and team.status == "RUNNING"
                and current_binding
                and current_consent
                and role_context is not None
                and role_context.role_context_hash == current_call.role_context_hash
                and role_context.consent_epoch == current_call.consent_epoch
                and role_context.consent_hash == current_call.consent_hash
            ):
                canceled_call = current_call.model_copy(
                    update={
                        "status": "canceled",
                        "completed_at": now,
                        "error_code": "TEAM_BINDING_INVALIDATED",
                    }
                )
                canceled_work = work.model_copy(
                    update={
                        "status": "CANCELED",
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": "TEAM_BINDING_INVALIDATED",
                        "updated_at": now,
                    }
                )
                conn.execute(
                    """UPDATE agent_team_model_calls SET status=?, payload=?
                       WHERE project_id=? AND model_call_id=? AND status='started'""",
                    (canceled_call.status, self._dump_model(canceled_call), project_id, model_call_id),
                )
                conn.execute(
                    """UPDATE agent_team_work_items SET status=?, payload=?, updated_at=?
                       WHERE project_id=? AND team_id=? AND work_item_id=? AND status='MODEL_STARTED'
                         AND json_extract(payload, '$.lease_owner')=?
                         AND json_extract(payload, '$.fencing_token')=?""",
                    (
                        canceled_work.status, self._dump_model(canceled_work), now.isoformat(),
                        project_id, work.team_id, work.work_item_id, owner, fencing_token,
                    ),
                )
                if team is not None and team.status in {"READY", "RUNNING", "AGGREGATING"}:
                    target = "NEEDS_ATTENTION" if current_binding else "CANCELED"
                    if team_transition_allowed(team.status, target):
                        updated_team = team.model_copy(update={"status": target, "updated_at": now})
                        conn.execute(
                            """UPDATE agent_teams SET status=?, payload=?, updated_at=?
                               WHERE project_id=? AND team_id=? AND status=?""",
                            (updated_team.status, self._dump_model(updated_team), now.isoformat(),
                             project_id, team.team_id, team.status),
                        )
                invalidation_event = AgentTeamEvent(
                    event_id=f"team_event_{uuid4().hex}",
                    team_id=current_call.team_id,
                    team_epoch=current_call.team_epoch,
                    project_id=project_id,
                    lifecycle_id=current_call.lifecycle_id,
                    command_id=f"failure-invalidated:{model_call_id}",
                    idempotency_key=f"failure-invalidated:{model_call_id}",
                    source="system",
                    event_type="TEAM_BINDING_INVALIDATED",
                    payload_hash=canonical_hash({"model_call_id": model_call_id}),
                    occurred_at=now,
                )
                self._insert_agent_team_event(conn, invalidation_event)
                if team is not None:
                    self._stop_agent_team_children(
                        conn,
                        team=team,
                        now=now,
                        error_code="TEAM_BINDING_INVALIDATED",
                    )
                return canceled_call

            if retry_wake is not None:
                if work.attempt_no != 1 or team.budget.max_repairs < 1:
                    raise RuntimeError("AGENT_TEAM_REPAIR_BUDGET_EXCEEDED")
                if not (
                    work_item_transition_allowed(work.status, "VALIDATING")
                    and work_item_transition_allowed("VALIDATING", "RETRY_READY")
                ):
                    raise RuntimeError("AGENT_TEAM_WORK_ITEM_TRANSITION_INVALID")
                updated_work = work.model_copy(
                    update={
                        "status": "RETRY_READY",
                        "attempt_no": 2,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": failed_call.error_code,
                        "updated_at": now,
                    }
                )
            else:
                target_status = "INDETERMINATE" if failed_call.status == "indeterminate" else "FAILED"
                if not work_item_transition_allowed(work.status, target_status):
                    raise RuntimeError("AGENT_TEAM_WORK_ITEM_TRANSITION_INVALID")
                updated_work = work.model_copy(
                    update={
                        "status": target_status,
                        "lease_owner": None,
                        "lease_expires_at": None,
                        "error_code": failed_call.error_code,
                        "updated_at": now,
                    }
                )

            cursor = conn.execute(
                """UPDATE agent_team_model_calls SET status=?, payload=?
                   WHERE project_id=? AND model_call_id=? AND status='started'""",
                (failed_call.status, self._dump_model(failed_call), project_id, model_call_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_MODEL_CALL_FAILURE_CAS_FAILED")
            cursor = conn.execute(
                """UPDATE agent_team_work_items SET status=?, payload=?, updated_at=?
                   WHERE project_id=? AND team_id=? AND work_item_id=? AND status='MODEL_STARTED'
                     AND json_extract(payload, '$.lease_owner')=?
                     AND json_extract(payload, '$.fencing_token')=?""",
                (
                    updated_work.status, self._dump_model(updated_work), now.isoformat(),
                    project_id, work.team_id, work.work_item_id, owner, fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_WORK_ITEM_FAILURE_CAS_FAILED")
            self._insert_agent_team_event(conn, event)
            if retry_wake is not None:
                self._insert_agent_team_wake(conn, retry_wake)
            else:
                attention_event = AgentTeamEvent(
                    event_id=f"team_event_{uuid4().hex}",
                    team_id=team.team_id,
                    team_epoch=team.team_epoch,
                    project_id=team.project_id,
                    lifecycle_id=team.lifecycle_id,
                    command_id=f"team-attention:{model_call_id}",
                    idempotency_key=f"team-attention:{model_call_id}",
                    source="system",
                    event_type="TEAM_NEEDS_ATTENTION",
                    payload_hash=canonical_hash({
                        "model_call_id": model_call_id,
                        "error_code": failed_call.error_code,
                    }),
                    occurred_at=now,
                )
                self._insert_agent_team_event(conn, attention_event)
                if team_transition_allowed(team.status, "NEEDS_ATTENTION"):
                    updated_team = team.model_copy(update={"status": "NEEDS_ATTENTION", "updated_at": now})
                    conn.execute(
                        """UPDATE agent_teams SET status=?, payload=?, updated_at=?
                           WHERE project_id=? AND team_id=? AND status=?""",
                        (updated_team.status, self._dump_model(updated_team), now.isoformat(),
                         project_id, team.team_id, team.status),
                    )
                self._stop_agent_team_children(
                    conn,
                    team=team,
                    now=now,
                    error_code=failed_call.error_code or "TEAM_SCHEDULER_FAILED",
                )
        return failed_call

    def complete_agent_team_aggregation(
        self,
        *,
        advisory: AgentTeamAdvisory,
        event: AgentTeamEvent,
        wake: AgentTeamWakeRecord,
        now: datetime,
    ) -> AgentTeamAdvisory:
        if (
            advisory.project_id != event.project_id
            or advisory.team_id != event.team_id
            or advisory.team_epoch != event.team_epoch
            or advisory.lifecycle_id != event.lifecycle_id
            or event.event_type != "TEAM_ADVISORY_FROZEN"
            or event.payload_hash != advisory.advisory_hash
            or wake.project_id != advisory.project_id
            or wake.team_id != advisory.team_id
            or wake.team_epoch != advisory.team_epoch
            or wake.lifecycle_id != advisory.lifecycle_id
            or wake.reason != "planning_resume"
            or wake.status != "PENDING"
        ):
            raise RuntimeError("AGENT_TEAM_AGGREGATION_BINDING_INVALID")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            team_row = conn.execute(
                "SELECT payload FROM agent_teams WHERE project_id=? AND team_id=?",
                (advisory.project_id, advisory.team_id),
            ).fetchone()
            team = AgentTeamRecord.model_validate_json(team_row["payload"]) if team_row else None
            if team is None:
                raise RuntimeError("AGENT_TEAM_NOT_FOUND")
            if team.status == "COMPLETED":
                existing_row = conn.execute(
                    "SELECT payload FROM agent_team_advisories WHERE project_id=? AND team_id=? AND advisory_id=?",
                    (advisory.project_id, advisory.team_id, team.advisory_id),
                ).fetchone()
                prior_wake = conn.execute(
                    "SELECT payload FROM agent_team_wake_outbox WHERE team_id=? AND step_key=?",
                    (advisory.team_id, wake.step_key),
                ).fetchone()
                prior_event = conn.execute(
                    "SELECT payload FROM agent_team_events WHERE team_id=? AND idempotency_key=?",
                    (event.team_id, event.idempotency_key),
                ).fetchone()
                existing = (
                    AgentTeamAdvisory.model_validate_json(existing_row["payload"])
                    if existing_row
                    else None
                )
                if (
                    existing is not None
                    and existing.advisory_hash == advisory.advisory_hash
                    and prior_wake is not None
                    and AgentTeamWakeRecord.model_validate_json(prior_wake["payload"]).reason
                    == "planning_resume"
                    and prior_event is not None
                    and AgentTeamEvent.model_validate_json(prior_event["payload"]).payload_hash
                    == event.payload_hash
                ):
                    return existing
                raise RuntimeError("AGENT_TEAM_AGGREGATION_REPLAYED")

            consent = self._current_project_multi_agent_consent(conn, advisory.project_id)
            if (
                team.status not in {"RUNNING", "AGGREGATING"}
                or team.team_epoch != advisory.team_epoch
                or team.lifecycle_id != advisory.lifecycle_id
                or team.snapshot_hash != advisory.context_snapshot_hash
                or team.role_registry_hash != advisory.role_registry_hash
                or team.context_projector_hash != advisory.context_projector_hash
                or team.aggregation_policy_hash != advisory.aggregation_policy_hash
                or team.model_profile_hash != advisory.model_profile_hash
                or team.consent_epoch != advisory.consent_epoch
                or team.consent_hash != advisory.consent_hash
                or not self._lifecycle_has_current_team_binding(conn, team)
                or consent is None
                or not consent.enabled
                or consent.consent_epoch != advisory.consent_epoch
                or consent.consent_hash != advisory.consent_hash
            ):
                raise RuntimeError("AGENT_TEAM_AGGREGATION_BINDING_INVALID")

            context_row = conn.execute(
                "SELECT payload FROM agent_team_contexts WHERE project_id=? AND team_id=? AND snapshot_hash=?",
                (advisory.project_id, advisory.team_id, advisory.context_snapshot_hash),
            ).fetchone()
            if context_row is None:
                raise RuntimeError("AGENT_TEAM_CONTEXT_NOT_FOUND")
            snapshot = AgentTeamContextSnapshot.model_validate_json(context_row["payload"])
            if (
                snapshot.base_context_hash != advisory.base_context_hash
                or snapshot.policy_version != advisory.policy_version
                or snapshot.redaction_policy_version != advisory.redaction_policy_version
            ):
                raise RuntimeError("AGENT_TEAM_AGGREGATION_BINDING_INVALID")
            work_rows = conn.execute(
                "SELECT payload FROM agent_team_work_items WHERE project_id=? AND team_id=?",
                (advisory.project_id, advisory.team_id),
            ).fetchall()
            work_items = [AgentTeamWorkItem.model_validate_json(row["payload"]) for row in work_rows]
            if len(work_items) != 3 or {item.status for item in work_items} != {"COMPLETED"}:
                raise RuntimeError("AGENT_TEAM_AGGREGATION_INCOMPLETE")
            finding_rows = conn.execute(
                "SELECT payload FROM agent_team_findings WHERE project_id=? AND team_id=?",
                (advisory.project_id, advisory.team_id),
            ).fetchall()
            findings = [AgentTeamFinding.model_validate_json(row["payload"]) for row in finding_rows]
            finding_ids = {finding.finding_id for finding in findings}
            coverage_by_role = {
                role: sum(finding.reviewer_kind == role for finding in findings)
                for role in {"science", "safety", "completeness"}
            }
            if (
                set(advisory.accepted_finding_ids) | set(advisory.rejected_finding_ids)
                != finding_ids
                or advisory.coverage_by_role != coverage_by_role
                or not advisory.safety_reviewer_present
                or advisory.decision
                != (
                    "accepted"
                    if not advisory.blocking_codes and not advisory.conflict_codes
                    else "rejected"
                )
            ):
                raise RuntimeError("AGENT_TEAM_AGGREGATION_CONTENT_INVALID")
            for item in work_items:
                call_row = conn.execute(
                    "SELECT payload FROM agent_team_model_calls WHERE project_id=? AND model_call_id=?",
                    (advisory.project_id, item.model_call_id),
                ).fetchone()
                call = AgentTeamModelCallRecord.model_validate_json(call_row["payload"]) if call_row else None
                if (
                    call is None
                    or call.status != "completed"
                    or call.work_item_id != item.work_item_id
                    or call.attempt_no != item.attempt_no
                ):
                    raise RuntimeError("AGENT_TEAM_AGGREGATION_CALL_INVALID")

            if team.status == "RUNNING" and not team_transition_allowed("RUNNING", "AGGREGATING"):
                raise RuntimeError("AGENT_TEAM_TRANSITION_INVALID")
            if not team_transition_allowed("AGGREGATING", "COMPLETED"):
                raise RuntimeError("AGENT_TEAM_TRANSITION_INVALID")
            updated_team = team.model_copy(update={
                "status": "COMPLETED",
                "advisory_id": advisory.advisory_id,
                "updated_at": now,
            })
            conn.execute(
                "INSERT INTO agent_team_advisories "
                "(advisory_id, team_id, team_epoch, project_id, lifecycle_id, context_snapshot_hash, advisory_hash, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (advisory.advisory_id, advisory.team_id, advisory.team_epoch,
                 advisory.project_id, advisory.lifecycle_id, advisory.context_snapshot_hash,
                 advisory.advisory_hash, self._dump_model(advisory), advisory.created_at.isoformat()),
            )
            cursor = conn.execute(
                "UPDATE agent_teams SET status=?, payload=?, updated_at=? "
                "WHERE project_id=? AND team_id=? AND status=?",
                (updated_team.status, self._dump_model(updated_team), now.isoformat(),
                 advisory.project_id, advisory.team_id, team.status),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_TEAM_AGGREGATION_CAS_FAILED")
            self._insert_agent_team_event(conn, event)
            self._insert_agent_team_wake(conn, wake)
        return advisory

    def bind_agent_planning_team(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
        *,
        expected_team_id: str | None,
        expected_team_epoch: int,
    ) -> AgentLifecycleRecord:
        if (
            record.planning_mode != "multi_agent"
            or not record.planning_team_id
            or record.planning_team_epoch != expected_team_epoch + 1
            or record.last_command_id != event.command_id
            or event.lifecycle_id != record.lifecycle_id
            or event.project_id != record.project_id
            or event.from_state != event.to_state
        ):
            raise RuntimeError("TEAM_BINDING_INVALIDATED")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=?",
                (record.lifecycle_id, record.project_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            current = _decode_agent_lifecycle(row["payload"])
            if (
                current.planning_team_id != expected_team_id
                or current.planning_team_epoch != expected_team_epoch
                or event.from_state != current.state
            ):
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            expected = current.model_copy(
                update={
                    "planning_mode": "multi_agent",
                    "planning_team_id": record.planning_team_id,
                    "planning_team_epoch": expected_team_epoch + 1,
                    "updated_at": record.updated_at,
                    "last_command_id": event.command_id,
                }
            )
            if record != expected:
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            cursor = conn.execute(
                """UPDATE agent_lifecycles SET payload=?, updated_at=?
                   WHERE lifecycle_id=? AND project_id=? AND state=?
                     AND json_extract(payload, '$.planning_mode')=?
                     AND json_extract(payload, '$.planning_team_id') IS ?
                     AND json_extract(payload, '$.planning_team_epoch')=?""",
                (
                    self._dump_model(record),
                    record.updated_at.isoformat(),
                    record.lifecycle_id,
                    record.project_id,
                    current.state,
                    current.planning_mode,
                    expected_team_id,
                    expected_team_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("TEAM_BINDING_INVALIDATED")
            self._insert_agent_lifecycle_event(conn, event)
        return record

    def transition_agent_lifecycle(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
        *,
        expected_state: str,
    ) -> AgentLifecycleRecord:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agent_lifecycles
                SET state = ?, payload = ?, updated_at = ?
                WHERE lifecycle_id = ? AND project_id = ? AND state = ?
                  AND json_extract(payload, '$.planning_mode') = ?
                  AND json_extract(payload, '$.planning_team_id') IS ?
                  AND json_extract(payload, '$.planning_team_epoch') = ?
                """,
                (
                    record.state,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.updated_at.isoformat(),
                    record.lifecycle_id,
                    record.project_id,
                    expected_state,
                    record.planning_mode,
                    record.planning_team_id,
                    record.planning_team_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LIFECYCLE_CONCURRENT_TRANSITION")
            self._insert_agent_lifecycle_event(conn, event)
        return record

    def transition_agent_lifecycle_with_wake(
        self, record: AgentLifecycleRecord, event: AgentLifecycleEvent, wake: AgentTaskWakeRecord,
        *, expected_state: str,
    ) -> AgentLifecycleRecord:
        """Commit a continuation-producing state transition with its outbox row."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE agent_lifecycles SET state=?, payload=?, updated_at=?
                   WHERE lifecycle_id=? AND project_id=? AND state=?
                     AND json_extract(payload, '$.planning_mode')=?
                     AND json_extract(payload, '$.planning_team_id') IS ?
                     AND json_extract(payload, '$.planning_team_epoch')=?""",
                (record.state, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                 record.updated_at.isoformat(), record.lifecycle_id, record.project_id, expected_state,
                 record.planning_mode, record.planning_team_id, record.planning_team_epoch),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LIFECYCLE_CONCURRENT_TRANSITION")
            self._insert_agent_lifecycle_event(conn, event)
            self._insert_agent_task_wake(conn, wake)
        return record

    def transition_agent_lifecycle_with_template(
        self, record: AgentLifecycleRecord, event: AgentLifecycleEvent, wake: AgentTaskWakeRecord,
        *, resource: dict[str, str], expected_batch_id: str,
    ) -> AgentLifecycleRecord:
        """Publish registration, invalidation, event and wake in one transaction."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM projects WHERE id=?", (record.project_id,)).fetchone()
            if row is None:
                raise RuntimeError("PROJECT_NOT_FOUND")
            project = ProjectDetail(**json.loads(row["payload"]))
            metadata = dict(project.metadata)
            defaults = dict(metadata.get("agent_defaults") or {})
            defaults.update(schema_version=1, default_template=resource)
            metadata["agent_defaults"] = defaults
            cursor = conn.execute(
                """UPDATE agent_lifecycles SET state=?, payload=?, updated_at=?
                   WHERE lifecycle_id=? AND project_id=? AND state='WAITING_FOR_SCIENCE_DECISION'
                   AND json_extract(payload, '$.pending_decision_batch.batch_id')=?
                   AND json_extract(payload, '$.planning_mode')=?
                   AND json_extract(payload, '$.planning_team_id') IS ?
                   AND json_extract(payload, '$.planning_team_epoch')=?""",
                (record.state, self._dump_model(record), record.updated_at.isoformat(),
                 record.lifecycle_id, record.project_id, expected_batch_id,
                 record.planning_mode, record.planning_team_id, record.planning_team_epoch),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_DECISION_STALE")
            conn.execute("UPDATE projects SET payload=? WHERE id=?", (
                self._dump_model(project.model_copy(update={"metadata": metadata})), record.project_id,
            ))
            self._insert_agent_lifecycle_event(conn, event)
            self._insert_agent_task_wake(conn, wake)
        return record

    def transition_agent_lifecycle_with_harness_action(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
        action: AgentActionRecord,
        *,
        expected_state: str,
        expected_action_status: str,
        duplicate_guard=None,
        duplicate_event: AgentLifecycleEvent | None = None,
    ) -> AgentLifecycleRecord:
        """Atomically publish a Harness business result and its action ledger."""
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if duplicate_guard is not None:
                row = conn.execute(
                    "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=? AND project_id=? AND state=?",
                    (record.lifecycle_id, record.project_id, expected_state),
                ).fetchone()
                if row is None:
                    raise RuntimeError("LIFECYCLE_CONCURRENT_TRANSITION")
                authoritative = _decode_agent_lifecycle(row["payload"])
                if duplicate_guard(authoritative):
                    rejected = action.model_copy(update={
                        "status": "rejected", "error_code": "AGENT_DECISION_ALREADY_CONFIRMED",
                        "decision_batch_id": None, "decision_batch_hash": None,
                    })
                    cursor = conn.execute(
                        "UPDATE agent_harness_actions SET status=?, payload=?, completed_at=? WHERE action_id=? AND attempt_id=? AND step_id=? AND status=?",
                        (rejected.status, rejected.model_dump_json(), rejected.completed_at.isoformat(),
                         rejected.action_id, rejected.attempt_id, rejected.step_id, expected_action_status),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("AGENT_HARNESS_ACTION_CONCURRENT_UPDATE")
                    if duplicate_event is None:
                        raise RuntimeError("AGENT_DECISION_DUPLICATE_EVENT_MISSING")
                    self._insert_agent_lifecycle_event(conn, duplicate_event)
                    return authoritative
            cursor = conn.execute(
                """UPDATE agent_lifecycles SET state=?, payload=?, updated_at=?
                   WHERE lifecycle_id=? AND project_id=? AND state=?
                     AND json_extract(payload, '$.planning_mode')=?
                     AND json_extract(payload, '$.planning_team_id') IS ?
                     AND json_extract(payload, '$.planning_team_epoch')=?""",
                (record.state, json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                 record.updated_at.isoformat(), record.lifecycle_id, record.project_id, expected_state,
                 record.planning_mode, record.planning_team_id, record.planning_team_epoch),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LIFECYCLE_CONCURRENT_TRANSITION")
            self._insert_agent_lifecycle_event(conn, event)
            cursor = conn.execute(
                """UPDATE agent_harness_actions SET status=?, payload=?, completed_at=?
                   WHERE action_id=? AND attempt_id=? AND step_id=? AND status=?""",
                (action.status, json.dumps(action.model_dump(mode="json"), ensure_ascii=False),
                 action.completed_at.isoformat() if action.completed_at else None,
                 action.action_id, action.attempt_id, action.step_id, expected_action_status),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AGENT_HARNESS_ACTION_CONCURRENT_UPDATE")
        return record

    def add_agent_lifecycle_event(self, event: AgentLifecycleEvent) -> AgentLifecycleEvent:
        with self._lock, self._connect() as conn:
            self._insert_agent_lifecycle_event(conn, event)
        return event

    def list_agent_lifecycle_events(
        self,
        lifecycle_id: str,
    ) -> list[AgentLifecycleEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM agent_lifecycle_events
                WHERE lifecycle_id = ? ORDER BY rowid
                """,
                (lifecycle_id,),
            ).fetchall()
        return [AgentLifecycleEvent(**json.loads(row["payload"])) for row in rows]

    def get_study_overview(self, study_id: str) -> StudyOverview | None:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM projects ORDER BY project_order, id").fetchall()
        for row in rows:
            project = self._load_payload(row["payload"], ProjectDetail)
            if project.study_id == study_id:
                subjects = project.subjects_count
                scans = project.scans_count
                dicom_subjects = 0
                dicom_series = 0
                dicom_files = 0
                if project.metadata:
                    try:
                        from src.backend.app.services.data_readiness import build_data_readiness
                        dr = build_data_readiness(project.id)
                        if dr.image_source_count > 0:
                            subjects = dr.subject_count
                            scans = dr.image_source_count
                        elif dr.dicom_file_count > 0 or dr.dicom_series_count > 0:
                            subjects = 0
                            scans = 0
                            dicom_subjects = dr.subject_count
                            dicom_series = dr.dicom_series_count
                            dicom_files = dr.dicom_file_count
                    except Exception:
                        subjects = project.subjects_count
                        scans = project.scans_count

                return StudyOverview(
                    project_id=project.id,
                    study_id=project.study_id,
                    study_name=project.name,
                    modality=project.modality,
                    sequences=project.sequences,
                    subjects=subjects,
                    scans=scans,
                    total_size=project.total_size,
                    date=project.created_date,
                    dicom_subjects=dicom_subjects,
                    dicom_series=dicom_series,
                    dicom_files=dicom_files,
                )
        return None

    def get_dataset_summary(self, project_id: str) -> DatasetSummary | None:
        project = self.get_project(project_id)
        if not project:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT health_status FROM dataset_health WHERE project_id = ?", (project_id,)).fetchone()

        subjects = project.subjects_count
        scans = project.scans_count
        dicom_subjects = 0
        dicom_series = 0
        dicom_files = 0
        if project.metadata:
            try:
                from src.backend.app.services.data_readiness import build_data_readiness
                dr = build_data_readiness(project_id)
                if dr.image_source_count > 0:
                    subjects = dr.subject_count
                    scans = dr.image_source_count
                elif dr.dicom_file_count > 0 or dr.dicom_series_count > 0:
                    subjects = 0
                    scans = 0
                    dicom_subjects = dr.subject_count
                    dicom_series = dr.dicom_series_count
                    dicom_files = dr.dicom_file_count
            except Exception:
                subjects = project.subjects_count
                scans = project.scans_count

        return DatasetSummary(
            project_id=project.id,
            subjects=subjects,
            scans=scans,
            total_size=project.total_size,
            health_status=row["health_status"] if row else "Unknown",
            dicom_subjects=dicom_subjects,
            dicom_series=dicom_series,
            dicom_files=dicom_files,
        )

    def get_model_status(self, project_id: str) -> ModelStatus | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM models WHERE project_id = ?", (project_id,)).fetchone()
        return self._load_payload(row["payload"], ModelStatus) if row else None

    def list_tasks(self) -> list[TaskLogEntry]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT payload FROM tasks ORDER BY updated_at DESC, id DESC").fetchall()
        return [TaskLogEntry(**self._load_task_payload(row["payload"]).model_dump()) for row in rows]

    def get_task(self, task_id: str) -> TaskDetail | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._load_task_payload(row["payload"]) if row else None

    def add_task(self, task: TaskDetail) -> TaskDetail:
        now = task.updated_at or utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (task.id, self._dump_model(task), now, now),
            )
        return task

    def update_task(self, task_id: str, **updates: object) -> TaskDetail | None:
        current = self.get_task(task_id)
        if not current:
            return None
        payload = current.model_dump()
        payload.update(updates)
        payload["updated_at"] = utc_now_iso()
        updated = TaskDetail(**payload)
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET payload = ?, updated_at = ? WHERE id = ?",
                (self._dump_model(updated), updated.updated_at, task_id),
            )
        return updated

    def append_task_event(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        progress: int,
        message: str,
        result_path: str | None = None,
        source: str = "task_manager",
        metadata: dict[str, object] | None = None,
    ) -> TaskEvent:
        timestamp = utc_now_iso()
        event = TaskEvent(
            id=0,
            task_id=task_id,
            status=status,
            progress=progress,
            message=message,
            timestamp=timestamp,
            result_path=result_path,
            source=source,
            metadata=dict(metadata or {}),
        )
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO task_events (task_id, payload, created_at) VALUES (?, ?, ?)",
                (task_id, self._dump_model(event, exclude={"id"}), timestamp),
            )
            event = event.model_copy(update={"id": int(cursor.lastrowid)})
        return event

    def list_task_events(self, task_id: str, limit: int = 200) -> list[TaskEvent]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, payload FROM task_events
                WHERE task_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        events: list[TaskEvent] = []
        for row in reversed(rows):
            payload = json.loads(row["payload"])
            payload["id"] = row["id"]
            events.append(TaskEvent(**payload))
        return events

    def add_approval(
        self,
        task_id: str,
        *,
        approved: bool,
        approved_by: str,
        approval_scope: str = "external_smoke_approved_run",
        safety_flags: dict[str, bool] | None = None,
    ) -> ApprovalRecord:
        approval = ApprovalRecord(
            approval_id=f"approval-{uuid4().hex[:10]}",
            task_id=task_id,
            approved=approved,
            approved_by=approved_by,
            approved_at=utc_now_iso(),
            approval_scope=approval_scope,
            safety_flags=dict(safety_flags or {}),
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals (approval_id, task_id, payload, created_at) VALUES (?, ?, ?, ?)",
                (approval.approval_id, task_id, self._dump_model(approval), approval.approved_at),
            )
        return approval

    def get_latest_approval(self, task_id: str) -> ApprovalRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM approvals
                WHERE task_id = ?
                ORDER BY created_at DESC, approval_id DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return ApprovalRecord(**json.loads(row["payload"])) if row else None

    def save_task_artifacts(self, task_id: str, payload: dict[str, object]) -> dict[str, object]:
        updated_at = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_artifacts (task_id, payload, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
                """,
                (task_id, json.dumps(payload, ensure_ascii=False), updated_at),
            )
        return payload

    def get_task_artifacts(self, task_id: str) -> dict[str, object]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM task_artifacts WHERE task_id = ?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else {}

    def import_dataset(self, request: DatasetImportRequest) -> DatasetImportResponse:
        if not self.get_project(request.project_id):
            raise KeyError(request.project_id)
        dataset_id = f"dataset-{uuid4().hex[:8]}"
        created_at = utc_now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO imports (dataset_id, project_id, path, dataset_type, created_at) VALUES (?, ?, ?, ?, ?)",
                (dataset_id, request.project_id, request.path, request.type, created_at),
            )
            conn.execute(
                """
                INSERT INTO dataset_health (project_id, health_status)
                VALUES (?, ?)
                ON CONFLICT(project_id) DO UPDATE SET health_status = excluded.health_status
                """,
                (request.project_id, "Imported"),
            )
        return DatasetImportResponse(success=True, dataset_id=dataset_id, message="Dataset imported")

    def list_import_paths(self, project_id: str) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT path FROM imports
                WHERE project_id = ?
                ORDER BY created_at DESC, dataset_id DESC
                """,
                (project_id,),
            ).fetchall()
        return [path for row in rows if (path := str(row["path"]).strip())]

    def list_import_records(self, project_id: str) -> list[dict[str, object]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dataset_id, project_id, path, dataset_type, created_at FROM imports
                WHERE project_id = ?
                ORDER BY created_at DESC, dataset_id DESC
                """,
                (project_id,),
            ).fetchall()
        return [
            {
                "dataset_id": row["dataset_id"],
                "project_id": row["project_id"],
                "path": row["path"],
                "dataset_type": row["dataset_type"],
                "created_at": row["created_at"],
                "exists": bool(path := str(row["path"]).strip()) and Path(path).exists(),
            }
            for row in rows
        ]

    def health_check(self) -> dict[str, object]:
        try:
            with self._lock, self._connect() as conn:
                project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
                task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
                event_count = conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
                approval_count = conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
                reviewed_plan_count = conn.execute("SELECT COUNT(*) FROM reviewed_plans").fetchone()[0]
                run_link_count = conn.execute("SELECT COUNT(*) FROM run_links").fetchone()[0]
            return {
                "name": "desktop_store",
                "ok": True,
                "path": str(self.db_path),
                "project_count": project_count,
                "task_count": task_count,
                "event_count": event_count,
                "approval_count": approval_count,
                "reviewed_plan_count": reviewed_plan_count,
                "run_link_count": run_link_count,
            }
        except Exception as exc:
            return {"name": "desktop_store", "ok": False, "path": str(self.db_path), "error": str(exc)}

    @staticmethod
    def _dump_model(model: object, exclude: set[str] | None = None) -> str:
        if hasattr(model, "model_dump"):
            payload = model.model_dump(mode="json", exclude=exclude or set())
        else:
            payload = dict(model)  # type: ignore[arg-type]
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _load_payload(payload: str, model_type: type[ProjectDetail] | type[ModelStatus]) -> ProjectDetail | ModelStatus:
        return model_type(**json.loads(payload))

    @staticmethod
    def _load_task_payload(payload: str) -> TaskDetail:
        data = json.loads(payload)
        data.setdefault("execution_mode", "simulated")
        return TaskDetail(**data)


mock_store = SQLiteDesktopStore()
