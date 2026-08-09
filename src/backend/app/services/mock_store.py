from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import (
    AgentHarnessAttempt,
    AgentHarnessContext,
    AgentHarnessStep,
)
from src.backend.app.schemas.agent_evidence import EvidenceSnapshot
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
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
from src.backend.app.schemas.execution_ticket import ExecutionTicket, ExecutionTicketEvent
from src.backend.app.schemas.gateway_dispatch import GatewayDispatch, GatewayDispatchEvent
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.observation import ObservationRecord
from src.backend.app.schemas.recovery import DiagnosisRecord, RecoveryProposal
from src.backend.app.schemas.recovery_attempt import (
    RecoveryApprovalEvent,
    RecoveryApprovalRecord,
    RecoveryAttemptEvent,
    RecoveryAttemptRecord,
    RecoveryQuotaReservation,
)

DEFAULT_STORE_PATH = Path("outputs/work/desktop/desktop_state.sqlite")


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


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
        return AgentLifecycleRecord(**json.loads(row["payload"])) if row else None

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

    def list_agent_lifecycles(self, project_id: str) -> list[AgentLifecycleRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload FROM agent_lifecycles
                WHERE project_id = ? ORDER BY updated_at DESC, lifecycle_id DESC
                """,
                (project_id,),
            ).fetchall()
        return [AgentLifecycleRecord(**json.loads(row["payload"])) for row in rows]

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
                """,
                (
                    record.state,
                    json.dumps(record.model_dump(mode="json"), ensure_ascii=False),
                    record.updated_at.isoformat(),
                    record.lifecycle_id,
                    record.project_id,
                    expected_state,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("LIFECYCLE_CONCURRENT_TRANSITION")
            self._insert_agent_lifecycle_event(conn, event)
        return record

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
