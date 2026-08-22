"""SQLite authority for project-scoped long-term memory.

The repository owns L3 learning memory only.  It stores references to canonical
project facts and never becomes an execution, approval, or scientific-state
authority.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.memory import (
    MemoryCandidate,
    MemoryEvent,
    MemoryImpactClass,
    MemoryItem,
    MemoryKind,
    MemoryRevision,
    MemorySource,
)

MEMORY_SCHEMA_VERSION = "memory-store-v1"
KEY_SCHEMA_VERSION = "memory-key-v1"
DEFAULT_POLICY_VERSION = "memory-policy-v1"
_MEMORY_INITIALIZATION_LOCK = threading.Lock()


class MemoryRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(UTC).isoformat().replace("+00:00", "Z")


def canonical_memory_key(kind: str, key: str) -> str:
    normalized = unicodedata.normalize("NFC", key.strip()).casefold()
    if not normalized:
        raise MemoryRepositoryError("MEMORY_KEY_REQUIRED")
    return f"{kind}:{normalized}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_candidates (
    candidate_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'project',
    source_trust_class TEXT NOT NULL,
    kind TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_text TEXT NOT NULL,
    impact_class TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_sequence INTEGER,
    consent_epoch INTEGER NOT NULL,
    extractor TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    status TEXT NOT NULL,
    requires_review INTEGER NOT NULL,
    rejection_code TEXT,
    dedupe_hash TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    candidate_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    consolidated_at TEXT,
    UNIQUE(project_id, dedupe_hash)
);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_project_status_created
    ON memory_candidates(project_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scope_type TEXT NOT NULL DEFAULT 'project',
    kind TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    current_revision_id TEXT NOT NULL,
    item_version INTEGER NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    superseded_by_memory_id TEXT,
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_one_active_key
    ON memory_items(project_id, canonical_key) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_memory_items_project_status_updated
    ON memory_items(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_revisions (
    revision_id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    generation INTEGER NOT NULL DEFAULT 0,
    content_json TEXT NOT NULL,
    content_text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    impact_class TEXT NOT NULL,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    confirmation_status TEXT NOT NULL,
    algorithm_id TEXT,
    algorithm_version TEXT,
    config_fingerprint TEXT,
    applicability_json TEXT NOT NULL,
    confirmation_event_id TEXT,
    change_reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, revision_number),
    FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);

CREATE TABLE IF NOT EXISTS memory_sources (
    source_link_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    source_trust_class TEXT NOT NULL,
    source_sequence INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(revision_id, source_type, source_id, source_hash),
    FOREIGN KEY(revision_id) REFERENCES memory_revisions(revision_id),
    FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);
CREATE INDEX IF NOT EXISTS idx_memory_sources_project_source
    ON memory_sources(project_id, source_type, source_id);

CREATE TABLE IF NOT EXISTS memory_events (
    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    memory_id TEXT,
    candidate_id TEXT,
    command_id TEXT,
    principal TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_hash TEXT,
    after_hash TEXT,
    details_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_events_project_sequence
    ON memory_events(project_id, event_sequence DESC);

CREATE TABLE IF NOT EXISTS memory_commands (
    command_id TEXT PRIMARY KEY,
    principal TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT NOT NULL,
    target_id TEXT,
    payload_hash TEXT NOT NULL,
    result_json TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_tombstones (
    tombstone_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    canonical_key TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    source_lineage_fingerprints_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    key_schema_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    generation INTEGER NOT NULL,
    forget_epoch INTEGER NOT NULL,
    forget_outbox_sequence INTEGER NOT NULL,
    principal TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_tombstones_project_key_generation
    ON memory_tombstones(project_id, canonical_key, generation DESC);

CREATE TABLE IF NOT EXISTS memory_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_sequence INTEGER,
    consent_epoch INTEGER NOT NULL,
    status TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    retry_after TEXT,
    outcome_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_ready
    ON memory_jobs(status, retry_after, project_id);

CREATE TABLE IF NOT EXISTS memory_watermarks (
    consumer TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_sequence INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    consent_epoch INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(consumer, project_id)
);

CREATE TABLE IF NOT EXISTS memory_consolidation_runs (
    consolidation_run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    consent_epoch INTEGER NOT NULL,
    input_watermark INTEGER NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    selection_diff_json TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    model TEXT,
    output_hash TEXT,
    status TEXT NOT NULL,
    error_code TEXT,
    fencing_token INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_leases (
    project_id TEXT PRIMARY KEY,
    lease_owner TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_id UNINDEXED,
    project_id UNINDEXED,
    revision_id UNINDEXED,
    canonical_key,
    content_text,
    tokenize='unicode61'
);
"""


class MemoryRepository:
    """Short-transaction SQLite repository; safe to construct per dependency call."""

    def __init__(self, db_path: str | Path, *, read_only: bool = False) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.read_only = read_only
        self._lock = threading.RLock()
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        if self.read_only and immediate:
            raise MemoryRepositoryError("MEMORY_STORE_READ_ONLY")
        target = self.db_path.as_uri() + "?mode=ro" if self.read_only else str(self.db_path)
        conn = sqlite3.connect(
            target,
            timeout=5.0,
            check_same_thread=False,
            uri=self.read_only,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        if not self.read_only:
            conn.execute("PRAGMA secure_delete=ON")
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        try:
            # FastAPI may resolve multiple Memory dependencies concurrently on
            # the first Settings render.  Repository instances have distinct
            # per-instance locks, so schema/WAL initialization also needs one
            # process-wide boundary to prevent a fresh SQLite file from being
            # initialized by several worker threads at once.
            with _MEMORY_INITIALIZATION_LOCK, self._lock, self.connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                row = conn.execute(
                    "SELECT value FROM store_meta WHERE key='schema_version'"
                ).fetchone()
                if row is not None and row["value"] != MEMORY_SCHEMA_VERSION:
                    raise MemoryRepositoryError(
                        "MEMORY_SCHEMA_UNSUPPORTED",
                        f"Unsupported memory schema: {row['value']}",
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO store_meta(key, value) VALUES('schema_version', ?)",
                    (MEMORY_SCHEMA_VERSION,),
                )
        except MemoryRepositoryError:
            raise
        except Exception as exc:
            raise MemoryRepositoryError("MEMORY_STORE_INIT_FAILED", str(exc)) from exc

    def health_check(self) -> dict[str, Any]:
        try:
            with self.connect() as conn:
                version = conn.execute(
                    "SELECT value FROM store_meta WHERE key='schema_version'"
                ).fetchone()
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                last_forget = conn.execute(
                    "SELECT value FROM store_meta WHERE key='last_forget_wal_truncate_at'"
                ).fetchone()
            return {
                "ok": version is not None and check == "ok",
                "schema_version": version["value"] if version else None,
                "integrity": check,
                "path": str(self.db_path),
                "last_forget_wal_truncate_at": (
                    last_forget["value"] if last_forget else None
                ),
            }
        except Exception as exc:
            return {"ok": False, "error_code": "MEMORY_STORE_UNHEALTHY", "detail": str(exc)}

    def operational_counts(self, *, project_id: str) -> dict[str, int]:
        """Return a read-only operational projection for one project."""

        with self.connect() as conn:
            jobs = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status='retry' THEN 1 ELSE 0 END) AS retry_jobs,
                    SUM(CASE WHEN status='dead_letter' THEN 1 ELSE 0 END) AS dead_letter_jobs
                FROM memory_jobs WHERE project_id=?
                """,
                (project_id,),
            ).fetchone()
            leases = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN lease_expires_at>strftime('%Y-%m-%dT%H:%M:%SZ','now') THEN 1 ELSE 0 END) AS active_leases,
                    SUM(CASE WHEN lease_expires_at<=strftime('%Y-%m-%dT%H:%M:%SZ','now') THEN 1 ELSE 0 END) AS expired_leases
                FROM memory_leases WHERE project_id=?
                """,
                (project_id,),
            ).fetchone()
        return {
            "retry_jobs": int(jobs["retry_jobs"] or 0),
            "dead_letter_jobs": int(jobs["dead_letter_jobs"] or 0),
            "active_leases": int(leases["active_leases"] or 0),
            "expired_leases": int(leases["expired_leases"] or 0),
        }

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _source_from_row(self, row: sqlite3.Row) -> MemorySource:
        return MemorySource(
            source_type=row["source_type"],
            source_id=row["source_id"],
            source_hash=row["source_hash"],
            source_ref=f"{row['source_type']}:{row['source_id']}",
            source_trust_class=row["source_trust_class"],
            source_sequence=row["source_sequence"],
        )

    def _revision_from_row(self, row: sqlite3.Row) -> MemoryRevision:
        return MemoryRevision(
            revision_id=row["revision_id"],
            memory_id=row["memory_id"],
            revision_number=row["revision_number"],
            generation=row["generation"],
            content=json.loads(row["content_json"]),
            content_text=row["content_text"],
            content_hash=row["content_hash"],
            impact_class=row["impact_class"],
            confidence=row["confidence"],
            sensitivity=row["sensitivity"],
            confirmation_status=row["confirmation_status"],
            algorithm_id=row["algorithm_id"],
            algorithm_version=row["algorithm_version"],
            config_fingerprint=row["config_fingerprint"],
            applicability=json.loads(row["applicability_json"]),
            confirmation_event_id=row["confirmation_event_id"],
            change_reason=row["change_reason"],
            created_at=self._parse_time(row["created_at"]),
        )

    def _item_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> MemoryItem:
        revision_row = conn.execute(
            "SELECT * FROM memory_revisions WHERE revision_id=? AND memory_id=?",
            (row["current_revision_id"], row["memory_id"]),
        ).fetchone()
        if revision_row is None:
            raise MemoryRepositoryError("MEMORY_REVISION_MISSING")
        source_rows = conn.execute(
            "SELECT * FROM memory_sources WHERE revision_id=? ORDER BY created_at, source_link_id",
            (row["current_revision_id"],),
        ).fetchall()
        return MemoryItem(
            memory_id=row["memory_id"],
            project_id=row["project_id"],
            kind=row["kind"],
            canonical_key=row["canonical_key"],
            current_revision_id=row["current_revision_id"],
            item_version=row["item_version"],
            generation=row["generation"],
            status=row["status"],
            pinned=bool(row["pinned"]),
            superseded_by_memory_id=row["superseded_by_memory_id"],
            valid_from=self._parse_time(row["valid_from"]),
            valid_until=self._parse_time(row["valid_until"]),
            created_by=row["created_by"],
            created_at=self._parse_time(row["created_at"]),
            updated_at=self._parse_time(row["updated_at"]),
            revision=self._revision_from_row(revision_row),
            sources=tuple(self._source_from_row(value) for value in source_rows),
        )

    def get_item(self, *, project_id: str, memory_id: str) -> MemoryItem | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE project_id=? AND memory_id=?",
                (project_id, memory_id),
            ).fetchone()
            return self._item_from_row(conn, row) if row else None

    def list_item_revisions(
        self, *, project_id: str, memory_id: str
    ) -> list[MemoryRevision]:
        with self.connect() as conn:
            owner = conn.execute(
                "SELECT 1 FROM memory_items WHERE project_id=? AND memory_id=?",
                (project_id, memory_id),
            ).fetchone()
            if owner is None:
                return []
            rows = conn.execute(
                "SELECT * FROM memory_revisions WHERE memory_id=? ORDER BY revision_number DESC",
                (memory_id,),
            ).fetchall()
        return [self._revision_from_row(row) for row in rows]

    def get_item_by_canonical_key(
        self, *, project_id: str, canonical_key: str
    ) -> MemoryItem | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE project_id=? AND canonical_key=?
                ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'forgotten' THEN 1 ELSE 2 END,
                         updated_at DESC LIMIT 1
                """,
                (project_id, canonical_key),
            ).fetchone()
            return self._item_from_row(conn, row) if row else None

    def list_items(
        self,
        *,
        project_id: str,
        status: str | None = "active",
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryItem]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_items WHERE "
                + " AND ".join(clauses)
                + " ORDER BY pinned DESC, updated_at DESC, memory_id LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
            return [self._item_from_row(conn, row) for row in rows]

    def retrieve_active_items(
        self,
        *,
        project_id: str,
        query: str,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[tuple[MemoryItem, float]]:
        """Return authoritative active rows after an FTS prefilter and SQL rejoin.

        FTS is only a candidate generator.  Every result is rejoined to the
        current item/revision and filtered by project, status, expiry, and
        sensitivity before it can leave the repository.
        """

        effective_now = utc_iso(now)
        tokens = tuple(
            dict.fromkeys(
                value.casefold()
                for value in re.findall(r"[\w-]{2,64}", query, flags=re.UNICODE)
            )
        )[:20]
        fts_query = " OR ".join(f'"{value.replace(chr(34), chr(34) * 2)}"' for value in tokens)
        bounded_limit = max(1, min(limit, 200))
        with self.connect() as conn:
            ranked: dict[str, float] = {}
            if fts_query:
                rows = conn.execute(
                    """
                    SELECT memory_id, bm25(memory_fts) AS score
                    FROM memory_fts
                    WHERE memory_fts MATCH ? AND project_id=?
                    ORDER BY score, memory_id LIMIT ?
                    """,
                    (fts_query, project_id, bounded_limit),
                ).fetchall()
                ranked.update(
                    (str(row["memory_id"]), float(-row["score"])) for row in rows
                )
            # Pinned items and exact canonical-key hits remain visible even when
            # the goal text contains no useful FTS token.
            key_terms = {value for value in tokens if value}
            base_rows = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE project_id=? AND status='active'
                  AND (valid_until IS NULL OR valid_until>?)
                ORDER BY pinned DESC, updated_at DESC, memory_id
                LIMIT ?
                """,
                (project_id, effective_now, bounded_limit),
            ).fetchall()
            selected: dict[str, sqlite3.Row] = {}
            for row in base_rows:
                memory_id = str(row["memory_id"])
                exact = any(term in str(row["canonical_key"]).casefold() for term in key_terms)
                if bool(row["pinned"]) or exact or memory_id in ranked or not tokens:
                    selected[memory_id] = row
                    if exact:
                        ranked[memory_id] = ranked.get(memory_id, 0.0) + 100.0
            items: list[tuple[MemoryItem, float]] = []
            for memory_id, row in selected.items():
                item = self._item_from_row(conn, row)
                if item.revision.sensitivity in {"restricted", "rejected"}:
                    continue
                items.append((item, ranked.get(memory_id, 0.0)))
        return sorted(
            items,
            key=lambda pair: (
                -int(pair[0].pinned),
                -int(
                    any(
                        source.source_trust_class == "explicit_user"
                        for source in pair[0].sources
                    )
                ),
                -pair[1],
                -pair[0].updated_at.timestamp(),
                pair[0].memory_id,
            ),
        )[:bounded_limit]

    def count_items(
        self, *, project_id: str, status: str | None = "active"
    ) -> int:
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS value FROM memory_items WHERE "
                + " AND ".join(clauses),
                tuple(params),
            ).fetchone()
        return int(row["value"])

    def list_events(
        self, *, project_id: str, limit: int = 100, after_sequence: int = 0
    ) -> list[MemoryEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_events
                WHERE project_id=? AND event_sequence>?
                ORDER BY event_sequence LIMIT ?
                """,
                (project_id, max(0, after_sequence), max(1, min(limit, 200))),
            ).fetchall()
        return [
            MemoryEvent(
                event_id=row["event_id"],
                project_id=row["project_id"],
                memory_id=row["memory_id"],
                candidate_id=row["candidate_id"],
                command_id=row["command_id"],
                principal=row["principal"],
                event_type=row["event_type"],
                before_hash=row["before_hash"],
                after_hash=row["after_hash"],
                details=json.loads(row["details_json"]),
                occurred_at=self._parse_time(row["occurred_at"]),
            )
            for row in rows
        ]

    def count_events(self, *, project_id: str, after_sequence: int = 0) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS value FROM memory_events WHERE project_id=? AND event_sequence>?",
                (project_id, max(0, after_sequence)),
            ).fetchone()
        return int(row["value"])

    @staticmethod
    def _command_result(
        conn: sqlite3.Connection,
        *,
        command_id: str,
        principal: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT principal, payload_hash, result_json FROM memory_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            return None
        if row["principal"] != principal or row["payload_hash"] != payload_hash:
            raise MemoryRepositoryError("MEMORY_COMMAND_CONFLICT")
        return json.loads(row["result_json"])

    @staticmethod
    def _record_command(
        conn: sqlite3.Connection,
        *,
        command_id: str,
        principal: str,
        action: str,
        project_id: str,
        target_id: str | None,
        payload_hash: str,
        result: dict[str, Any],
        completed_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO memory_commands
                (command_id, principal, action, project_id, target_id,
                 payload_hash, result_json, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                principal,
                action,
                project_id,
                target_id,
                payload_hash,
                canonical_json(result),
                completed_at,
            ),
        )

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        *,
        project_id: str,
        principal: str,
        event_type: str,
        command_id: str | None = None,
        memory_id: str | None = None,
        candidate_id: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        details: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        event_id = f"memory_event_{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO memory_events
                (event_id, project_id, memory_id, candidate_id, command_id,
                 principal, event_type, before_hash, after_hash, details_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                project_id,
                memory_id,
                candidate_id,
                command_id,
                principal,
                event_type,
                before_hash,
                after_hash,
                canonical_json(details or {}),
                occurred_at or utc_iso(),
            ),
        )
        return event_id

    def remember_explicit(
        self,
        *,
        project_id: str,
        command_id: str,
        principal: str,
        kind: MemoryKind,
        key: str,
        value: dict[str, Any],
        summary: str,
        impact_class: MemoryImpactClass,
        consent_epoch: int,
        valid_until: str | None = None,
        candidate_expires_at: str | None = None,
    ) -> dict[str, Any]:
        canonical_key = canonical_memory_key(kind, key)
        command_payload = {
            "project_id": project_id,
            "kind": kind,
            "canonical_key": canonical_key,
            "value": value,
            "summary": summary,
            "impact_class": impact_class,
            "consent_epoch": consent_epoch,
        }
        payload_hash = stable_hash(command_payload)
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            replay = self._command_result(
                conn,
                command_id=command_id,
                principal=principal,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
            tombstone = conn.execute(
                "SELECT 1 FROM memory_tombstones WHERE project_id=? AND canonical_key=? LIMIT 1",
                (project_id, canonical_key),
            ).fetchone()
            auto_active = kind in {"user_preference", "presentation_preference"} and impact_class == "presentation"
            if auto_active and tombstone is None:
                result = self._insert_active_item(
                    conn,
                    project_id=project_id,
                    principal=principal,
                    command_id=command_id,
                    kind=kind,
                    canonical_key=canonical_key,
                    value=value,
                    summary=summary,
                    impact_class=impact_class,
                    source_type="explicit_remember",
                    source_id=command_id,
                    source_hash=payload_hash,
                    source_trust_class="explicit_user",
                    source_sequence=None,
                    change_reason="explicit_remember",
                    now=now,
                    valid_until=valid_until,
                )
            else:
                result = self._insert_candidate(
                    conn,
                    project_id=project_id,
                    kind=kind,
                    canonical_key=canonical_key,
                    value=value,
                    summary=summary,
                    impact_class=impact_class,
                    source_type="explicit_remember",
                    source_id=command_id,
                    source_hash=payload_hash,
                    source_trust_class="explicit_user",
                    source_sequence=None,
                    consent_epoch=consent_epoch,
                    extractor="explicit_command",
                    requires_review=True,
                    status="suppressed" if tombstone is not None else "proposed",
                    rejection_code="MEMORY_TOMBSTONE_SUPPRESSED" if tombstone else None,
                    now=now,
                    expires_at=candidate_expires_at,
                )
            self._record_command(
                conn,
                command_id=command_id,
                principal=principal,
                action="remember",
                project_id=project_id,
                target_id=result.get("memory_id") or result.get("candidate_id"),
                payload_hash=payload_hash,
                result=result,
                completed_at=now,
            )
            return result

    def _insert_active_item(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        principal: str,
        command_id: str | None,
        kind: str,
        canonical_key: str,
        value: dict[str, Any],
        summary: str,
        impact_class: str,
        source_type: str,
        source_id: str,
        source_hash: str,
        source_trust_class: str,
        source_sequence: int | None,
        change_reason: str,
        now: str,
        valid_until: str | None = None,
    ) -> dict[str, Any]:
        existing = conn.execute(
            "SELECT memory_id FROM memory_items WHERE project_id=? AND canonical_key=? AND status='active'",
            (project_id, canonical_key),
        ).fetchone()
        if existing is not None:
            raise MemoryRepositoryError("MEMORY_ACTIVE_KEY_CONFLICT")
        memory_id = f"memory_{uuid4().hex}"
        revision_id = f"memory_revision_{uuid4().hex}"
        content_hash = stable_hash({"value": value, "summary": summary})
        scientific = impact_class == "scientific" and kind == "project_decision"
        algorithm_id = "confirmed-project-decision" if scientific else None
        algorithm_version = "1" if scientific else None
        config_fingerprint = (
            stable_hash({"decision_kind": value.get("decision_kind"), "value": value.get("value")})
            if scientific
            else None
        )
        confirmation_event_id = source_id if scientific else None
        conn.execute(
            """
            INSERT INTO memory_items
                (memory_id, project_id, scope_type, kind, canonical_key,
                 current_revision_id, item_version, generation, status, pinned,
                 valid_from, valid_until, created_by, created_at, updated_at)
            VALUES (?, ?, 'project', ?, ?, ?, 1, 0, 'active', 0, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                project_id,
                kind,
                canonical_key,
                revision_id,
                now,
                valid_until,
                principal,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_revisions
                (revision_id, memory_id, revision_number, generation, content_json, content_text,
                 content_hash, impact_class, confidence, sensitivity,
                 confirmation_status, algorithm_id, algorithm_version,
                 config_fingerprint, applicability_json, confirmation_event_id,
                 change_reason, created_at)
            VALUES (?, ?, 1, 0, ?, ?, ?, ?, 1.0, 'project_internal',
                    'confirmed', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                memory_id,
                canonical_json(value),
                summary,
                content_hash,
                impact_class,
                algorithm_id,
                algorithm_version,
                config_fingerprint,
                canonical_json({"project_id": project_id}),
                confirmation_event_id,
                change_reason,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO memory_sources
                (source_link_id, revision_id, memory_id, project_id, source_type,
                 source_id, source_hash, source_trust_class, source_sequence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"memory_source_{uuid4().hex}",
                revision_id,
                memory_id,
                project_id,
                source_type,
                source_id,
                source_hash,
                source_trust_class,
                source_sequence,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO memory_fts(memory_id, project_id, revision_id, canonical_key, content_text) VALUES (?, ?, ?, ?, ?)",
            (memory_id, project_id, revision_id, canonical_key, summary),
        )
        event_id = self._insert_event(
            conn,
            project_id=project_id,
            principal=principal,
            event_type="accepted",
            command_id=command_id,
            memory_id=memory_id,
            after_hash=content_hash,
            details={"kind": kind, "canonical_key": canonical_key},
            occurred_at=now,
        )
        return {
            "status": "active",
            "memory_id": memory_id,
            "revision_id": revision_id,
            "event_id": event_id,
            "content_hash": content_hash,
        }

    def _insert_candidate(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        kind: str,
        canonical_key: str,
        value: dict[str, Any],
        summary: str,
        impact_class: str,
        source_type: str,
        source_id: str,
        source_hash: str,
        source_trust_class: str,
        source_sequence: int | None,
        consent_epoch: int,
        extractor: str,
        requires_review: bool,
        status: str,
        rejection_code: str | None,
        now: str,
        expires_at: str | None = None,
        confidence: float = 1.0,
        model: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        candidate_payload = {
            "project_id": project_id,
            "kind": kind,
            "canonical_key": canonical_key,
            "value": value,
            "summary": summary,
            "impact_class": impact_class,
        }
        dedupe_hash = stable_hash(
            {
                "project_id": project_id,
                "source_type": source_type,
                "source_id": source_id,
                "source_hash": source_hash,
                "extractor": extractor,
                "policy_version": DEFAULT_POLICY_VERSION,
                "candidate": candidate_payload,
            }
        )
        existing = conn.execute(
            "SELECT candidate_id, status, candidate_hash FROM memory_candidates WHERE project_id=? AND dedupe_hash=?",
            (project_id, dedupe_hash),
        ).fetchone()
        if existing is not None:
            return {
                "status": existing["status"],
                "candidate_id": existing["candidate_id"],
                "candidate_hash": existing["candidate_hash"],
                "deduplicated": True,
            }
        candidate_id = f"memory_candidate_{uuid4().hex}"
        candidate_hash = stable_hash(candidate_payload)
        conn.execute(
            """
            INSERT INTO memory_candidates
                (candidate_id, project_id, scope_type, source_trust_class, kind,
                 canonical_key, content_json, content_text, impact_class,
                 source_type, source_id, source_hash, source_sequence, consent_epoch,
                 extractor, policy_version, model, prompt_version, confidence, sensitivity, status,
                 requires_review, rejection_code, dedupe_hash, candidate_hash,
                 candidate_version, created_at, expires_at)
            VALUES (?, ?, 'project', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'project_internal', ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                candidate_id,
                project_id,
                source_trust_class,
                kind,
                canonical_key,
                canonical_json(value),
                summary,
                impact_class,
                source_type,
                source_id,
                source_hash,
                source_sequence,
                consent_epoch,
                extractor,
                DEFAULT_POLICY_VERSION,
                model,
                prompt_version,
                confidence,
                status,
                1 if requires_review else 0,
                rejection_code,
                dedupe_hash,
                candidate_hash,
                now,
                expires_at,
            ),
        )
        self._insert_event(
            conn,
            project_id=project_id,
            principal="memory-worker" if source_trust_class != "explicit_user" else "local-user",
            event_type="candidate_created" if status == "proposed" else "candidate_suppressed",
            candidate_id=candidate_id,
            after_hash=candidate_hash,
            details={"status": status, "rejection_code": rejection_code},
            occurred_at=now,
        )
        return {
            "status": status,
            "candidate_id": candidate_id,
            "candidate_hash": candidate_hash,
            "deduplicated": False,
        }

    def list_candidates(
        self, *, project_id: str, status: str = "proposed", limit: int = 100
    ) -> list[MemoryCandidate]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM memory_candidates
                WHERE project_id=? AND status=?
                ORDER BY created_at DESC, candidate_id LIMIT ?
                """,
                (project_id, status, max(1, min(limit, 200))),
            ).fetchall()
        return [self._candidate_from_row(row) for row in rows]

    def count_candidates(self, *, project_id: str, status: str = "proposed") -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS value FROM memory_candidates WHERE project_id=? AND status=?",
                (project_id, status),
            ).fetchone()
        return int(row["value"])

    def get_candidate(
        self, *, project_id: str, candidate_id: str
    ) -> MemoryCandidate | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_candidates WHERE project_id=? AND candidate_id=?",
                (project_id, candidate_id),
            ).fetchone()
        return self._candidate_from_row(row) if row else None

    def _candidate_from_row(self, row: sqlite3.Row) -> MemoryCandidate:
        source = MemorySource(
            source_type=row["source_type"],
            source_id=row["source_id"],
            source_hash=row["source_hash"],
            source_ref=f"{row['source_type']}:{row['source_id']}",
            source_trust_class=row["source_trust_class"],
            source_sequence=row["source_sequence"],
        )
        return MemoryCandidate(
            candidate_id=row["candidate_id"],
            project_id=row["project_id"],
            kind=row["kind"],
            canonical_key=row["canonical_key"],
            content=json.loads(row["content_json"]),
            content_text=row["content_text"],
            impact_class=row["impact_class"],
            source=source,
            consent_epoch=row["consent_epoch"],
            extractor=row["extractor"],
            policy_version=row["policy_version"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            confidence=row["confidence"],
            sensitivity=row["sensitivity"],
            status=row["status"],
            requires_review=bool(row["requires_review"]),
            rejection_code=row["rejection_code"],
            dedupe_hash=row["dedupe_hash"],
            candidate_hash=row["candidate_hash"],
            candidate_version=row["candidate_version"],
            created_at=self._parse_time(row["created_at"]),
            expires_at=self._parse_time(row["expires_at"]),
        )

    def get_watermark(self, *, consumer: str, project_id: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_watermarks WHERE consumer=? AND project_id=?",
                (consumer, project_id),
            ).fetchone()
        if row is None:
            return {
                "consumer": consumer,
                "project_id": project_id,
                "source_sequence": 0,
                "source_hash": "",
                "consent_epoch": 0,
                "updated_at": None,
            }
        return dict(row)

    def commit_source_result(
        self,
        *,
        consumer: str,
        project_id: str,
        source_sequence: int,
        source_hash: str,
        consent_epoch: int,
        source_type: str,
        source_id: str,
        source_trust_class: str,
        outcome: str,
        rejection_code: str | None = None,
        candidate: dict[str, Any] | None = None,
        candidate_expires_at: str | None = None,
    ) -> dict[str, Any]:
        """Commit Phase 1 result and watermark in one memory DB transaction."""

        now = utc_iso()
        job_id = f"phase1:{project_id}:{source_sequence}:{consent_epoch}"
        with self._lock, self.connect(immediate=True) as conn:
            current = conn.execute(
                "SELECT source_sequence, consent_epoch FROM memory_watermarks WHERE consumer=? AND project_id=?",
                (consumer, project_id),
            ).fetchone()
            if current is not None and int(current["source_sequence"]) >= source_sequence:
                return {
                    "status": "already_processed",
                    "source_sequence": int(current["source_sequence"]),
                }
            candidate_result: dict[str, Any] | None = None
            if candidate is not None:
                candidate_result = self._insert_candidate(
                    conn,
                    project_id=project_id,
                    kind=str(candidate["kind"]),
                    canonical_key=str(candidate["canonical_key"]),
                    value=dict(candidate["value"]),
                    summary=str(candidate["summary"]),
                    impact_class=str(candidate["impact_class"]),
                    source_type=source_type,
                    source_id=source_id,
                    source_hash=source_hash,
                    source_trust_class=source_trust_class,
                    source_sequence=source_sequence,
                    consent_epoch=consent_epoch,
                    extractor=str(candidate.get("extractor") or "deterministic-v1"),
                    requires_review=bool(candidate.get("requires_review", True)),
                    status=str(candidate.get("status") or "proposed"),
                    rejection_code=(
                        str(candidate["rejection_code"])
                        if candidate.get("rejection_code")
                        else None
                    ),
                    now=now,
                    expires_at=candidate_expires_at,
                    confidence=float(candidate.get("confidence", 1.0)),
                    model=(str(candidate["model"]) if candidate.get("model") else None),
                    prompt_version=(
                        str(candidate["prompt_version"])
                        if candidate.get("prompt_version")
                        else None
                    ),
                )
            conn.execute(
                """
                INSERT INTO memory_jobs
                    (job_id, job_type, project_id, source_sequence, consent_epoch,
                     status, attempt, last_error_code, outcome_json, created_at, updated_at)
                VALUES (?, 'phase1_extract', ?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    last_error_code=excluded.last_error_code,
                    outcome_json=excluded.outcome_json,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    project_id,
                    source_sequence,
                    consent_epoch,
                    outcome,
                    rejection_code,
                    canonical_json(
                        {
                            "source_type": source_type,
                            "source_id": source_id,
                            "candidate_id": (
                                candidate_result.get("candidate_id")
                                if candidate_result
                                else None
                            ),
                        }
                    ),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_watermarks
                    (consumer, project_id, source_sequence, source_hash,
                     consent_epoch, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(consumer, project_id) DO UPDATE SET
                    source_sequence=excluded.source_sequence,
                    source_hash=excluded.source_hash,
                    consent_epoch=excluded.consent_epoch,
                    updated_at=excluded.updated_at
                """,
                (
                    consumer,
                    project_id,
                    source_sequence,
                    source_hash,
                    consent_epoch,
                    now,
                ),
            )
            return {
                "status": outcome,
                "source_sequence": source_sequence,
                "candidate": candidate_result,
                "rejection_code": rejection_code,
            }

    def record_source_failure(
        self,
        *,
        project_id: str,
        source_sequence: int,
        consent_epoch: int,
        error_code: str,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        now_dt = utc_now()
        now = utc_iso(now_dt)
        job_id = f"phase1:{project_id}:{source_sequence}:{consent_epoch}"
        with self._lock, self.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT attempt FROM memory_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            attempt = int(row["attempt"]) + 1 if row else 1
            status = "dead_letter" if attempt >= max_attempts else "retry"
            retry_after = (
                None
                if status == "dead_letter"
                else utc_iso(now_dt + timedelta(seconds=min(300, 2**attempt)))
            )
            conn.execute(
                """
                INSERT INTO memory_jobs
                    (job_id, job_type, project_id, source_sequence, consent_epoch,
                     status, attempt, last_error_code, retry_after,
                     outcome_json, created_at, updated_at)
                VALUES (?, 'phase1_extract', ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    status=excluded.status,
                    attempt=excluded.attempt,
                    last_error_code=excluded.last_error_code,
                    retry_after=excluded.retry_after,
                    updated_at=excluded.updated_at
                """,
                (
                    job_id,
                    project_id,
                    source_sequence,
                    consent_epoch,
                    status,
                    attempt,
                    error_code,
                    retry_after,
                    now,
                    now,
                ),
            )
            return {
                "status": status,
                "attempt": attempt,
                "retry_after": retry_after,
                "error_code": error_code,
            }

    def review_candidate(
        self,
        *,
        project_id: str,
        candidate_id: str,
        command_id: str,
        principal: str,
        accept: bool,
        expected_candidate_version: int,
        candidate_hash: str,
        edited_value: dict[str, Any] | None = None,
        edited_summary: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "project_id": project_id,
            "candidate_id": candidate_id,
            "accept": accept,
            "expected_candidate_version": expected_candidate_version,
            "candidate_hash": candidate_hash,
            "edited_value": edited_value,
            "edited_summary": edited_summary,
            "reason": reason,
        }
        payload_hash = stable_hash(payload)
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            replay = self._command_result(
                conn,
                command_id=command_id,
                principal=principal,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
            row = conn.execute(
                "SELECT * FROM memory_candidates WHERE project_id=? AND candidate_id=?",
                (project_id, candidate_id),
            ).fetchone()
            if row is None:
                raise MemoryRepositoryError("MEMORY_CANDIDATE_NOT_FOUND")
            if row["status"] != "proposed":
                raise MemoryRepositoryError("MEMORY_CANDIDATE_NOT_REVIEWABLE")
            if (
                int(row["candidate_version"]) != expected_candidate_version
                or row["candidate_hash"] != candidate_hash
            ):
                raise MemoryRepositoryError("MEMORY_CANDIDATE_STALE")
            value = edited_value if edited_value is not None else json.loads(row["content_json"])
            summary = edited_summary if edited_summary is not None else row["content_text"]
            next_hash = stable_hash(
                {
                    "project_id": project_id,
                    "kind": row["kind"],
                    "canonical_key": row["canonical_key"],
                    "value": value,
                    "summary": summary,
                    "impact_class": row["impact_class"],
                }
            )
            next_status = "accepted" if accept else "rejected"
            next_version = int(row["candidate_version"]) + 1
            conn.execute(
                """
                UPDATE memory_candidates
                SET content_json=?, content_text=?, candidate_hash=?, status=?,
                    candidate_version=?, rejection_code=?
                WHERE candidate_id=? AND project_id=? AND candidate_version=?
                """,
                (
                    canonical_json(value),
                    summary,
                    next_hash,
                    next_status,
                    next_version,
                    None if accept else (reason or "USER_REJECTED"),
                    candidate_id,
                    project_id,
                    expected_candidate_version,
                ),
            )
            event_id = self._insert_event(
                conn,
                project_id=project_id,
                principal=principal,
                event_type="accepted" if accept else "rejected",
                candidate_id=candidate_id,
                command_id=command_id,
                before_hash=candidate_hash,
                after_hash=next_hash,
                details={"reason": reason},
                occurred_at=now,
            )
            result = {
                "status": next_status,
                "candidate_id": candidate_id,
                "candidate_version": next_version,
                "candidate_hash": next_hash,
                "event_id": event_id,
            }
            self._record_command(
                conn,
                command_id=command_id,
                principal=principal,
                action="accept_candidate" if accept else "reject_candidate",
                project_id=project_id,
                target_id=candidate_id,
                payload_hash=payload_hash,
                result=result,
                completed_at=now,
            )
            return result

    def set_pinned(
        self,
        *,
        project_id: str,
        memory_id: str,
        command_id: str,
        principal: str,
        expected_item_version: int,
        pinned: bool,
    ) -> dict[str, Any]:
        payload_hash = stable_hash(
            {
                "project_id": project_id,
                "memory_id": memory_id,
                "expected_item_version": expected_item_version,
                "pinned": pinned,
            }
        )
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            replay = self._command_result(
                conn,
                command_id=command_id,
                principal=principal,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
            row = conn.execute(
                "SELECT * FROM memory_items WHERE project_id=? AND memory_id=?",
                (project_id, memory_id),
            ).fetchone()
            if row is None:
                raise MemoryRepositoryError("MEMORY_ITEM_NOT_FOUND")
            if int(row["item_version"]) != expected_item_version:
                raise MemoryRepositoryError("MEMORY_ITEM_STALE")
            next_version = expected_item_version + 1
            conn.execute(
                "UPDATE memory_items SET pinned=?, item_version=?, updated_at=? WHERE project_id=? AND memory_id=? AND item_version=?",
                (
                    1 if pinned else 0,
                    next_version,
                    now,
                    project_id,
                    memory_id,
                    expected_item_version,
                ),
            )
            event_id = self._insert_event(
                conn,
                project_id=project_id,
                principal=principal,
                event_type="pinned" if pinned else "unpinned",
                command_id=command_id,
                memory_id=memory_id,
                details={"pinned": pinned},
                occurred_at=now,
            )
            result = {
                "status": "active",
                "memory_id": memory_id,
                "item_version": next_version,
                "pinned": pinned,
                "event_id": event_id,
            }
            self._record_command(
                conn,
                command_id=command_id,
                principal=principal,
                action="set_pinned",
                project_id=project_id,
                target_id=memory_id,
                payload_hash=payload_hash,
                result=result,
                completed_at=now,
            )
            return result

    def claim_project_lease(
        self, *, project_id: str, owner: str, ttl_seconds: int = 60
    ) -> dict[str, Any]:
        ttl_seconds = max(5, min(ttl_seconds, 600))
        modifier = f"+{ttl_seconds} seconds"
        with self._lock, self.connect(immediate=True) as conn:
            now = conn.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            ).fetchone()[0]
            expires = conn.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)", (modifier,)
            ).fetchone()[0]
            row = conn.execute(
                "SELECT * FROM memory_leases WHERE project_id=?", (project_id,)
            ).fetchone()
            if row is None:
                token = 1
                conn.execute(
                    "INSERT INTO memory_leases(project_id, lease_owner, lease_expires_at, fencing_token, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (project_id, owner, expires, token, now),
                )
            elif row["lease_owner"] == owner or row["lease_expires_at"] <= now:
                token = int(row["fencing_token"]) + 1
                conn.execute(
                    "UPDATE memory_leases SET lease_owner=?, lease_expires_at=?, fencing_token=?, updated_at=? WHERE project_id=?",
                    (owner, expires, token, now, project_id),
                )
            else:
                raise MemoryRepositoryError("MEMORY_CONSOLIDATION_LEASE_BUSY")
            return {
                "project_id": project_id,
                "owner": owner,
                "fencing_token": token,
                "lease_expires_at": expires,
            }

    def heartbeat_project_lease(
        self,
        *,
        project_id: str,
        owner: str,
        fencing_token: int,
        ttl_seconds: int = 60,
    ) -> dict[str, Any]:
        ttl_seconds = max(5, min(ttl_seconds, 600))
        with self._lock, self.connect(immediate=True) as conn:
            now = conn.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            ).fetchone()[0]
            expires = conn.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)",
                (f"+{ttl_seconds} seconds",),
            ).fetchone()[0]
            cursor = conn.execute(
                """
                UPDATE memory_leases SET lease_expires_at=?, updated_at=?
                WHERE project_id=? AND lease_owner=? AND fencing_token=?
                  AND lease_expires_at>?
                """,
                (expires, now, project_id, owner, fencing_token, now),
            )
            if cursor.rowcount != 1:
                raise MemoryRepositoryError("MEMORY_CONSOLIDATION_LEASE_STALE")
            return {
                "project_id": project_id,
                "owner": owner,
                "fencing_token": fencing_token,
                "lease_expires_at": expires,
            }

    def release_project_lease(
        self, *, project_id: str, owner: str, fencing_token: int
    ) -> bool:
        with self._lock, self.connect(immediate=True) as conn:
            cursor = conn.execute(
                "DELETE FROM memory_leases WHERE project_id=? AND lease_owner=? AND fencing_token=?",
                (project_id, owner, fencing_token),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _assert_lease(
        conn: sqlite3.Connection,
        *,
        project_id: str,
        owner: str,
        fencing_token: int,
    ) -> None:
        row = conn.execute(
            """
            SELECT 1 FROM memory_leases
            WHERE project_id=? AND lease_owner=? AND fencing_token=?
              AND lease_expires_at>strftime('%Y-%m-%dT%H:%M:%SZ','now')
            """,
            (project_id, owner, fencing_token),
        ).fetchone()
        if row is None:
            raise MemoryRepositoryError("MEMORY_CONSOLIDATION_LEASE_STALE")

    def consolidate_accepted(
        self,
        *,
        project_id: str,
        consent_epoch: int,
        owner: str,
        fencing_token: int,
        policy_version: str = DEFAULT_POLICY_VERSION,
        valid_until: str | None = None,
    ) -> dict[str, Any]:
        now = utc_iso()
        run_id = f"memory_consolidation_{uuid4().hex}"
        diff: dict[str, list[str]] = {
            "added": [],
            "retained": [],
            "superseded": [],
            "expired": [],
            "removed": [],
            "needs_review": [],
        }
        with self._lock, self.connect(immediate=True) as conn:
            self._assert_lease(
                conn,
                project_id=project_id,
                owner=owner,
                fencing_token=fencing_token,
            )
            rows = conn.execute(
                """
                SELECT * FROM memory_candidates
                WHERE project_id=? AND status='accepted' AND consolidated_at IS NULL
                  AND consent_epoch=?
                ORDER BY created_at, candidate_id
                """,
                (project_id, consent_epoch),
            ).fetchall()
            candidate_ids = [str(row["candidate_id"]) for row in rows]
            for candidate in rows:
                tombstone = conn.execute(
                    "SELECT 1 FROM memory_tombstones WHERE project_id=? AND canonical_key=? LIMIT 1",
                    (project_id, candidate["canonical_key"]),
                ).fetchone()
                if tombstone is not None:
                    conn.execute(
                        "UPDATE memory_candidates SET status='suppressed', rejection_code='MEMORY_TOMBSTONE_SUPPRESSED', consolidated_at=? WHERE candidate_id=?",
                        (now, candidate["candidate_id"]),
                    )
                    diff["removed"].append(candidate["candidate_id"])
                    continue
                active = conn.execute(
                    "SELECT * FROM memory_items WHERE project_id=? AND canonical_key=? AND status='active'",
                    (project_id, candidate["canonical_key"]),
                ).fetchone()
                candidate_content_hash = stable_hash(
                    {
                        "value": json.loads(candidate["content_json"]),
                        "summary": candidate["content_text"],
                    }
                )
                if active is None:
                    result = self._insert_active_item(
                        conn,
                        project_id=project_id,
                        principal="memory-consolidator",
                        command_id=None,
                        kind=candidate["kind"],
                        canonical_key=candidate["canonical_key"],
                        value=json.loads(candidate["content_json"]),
                        summary=candidate["content_text"],
                        impact_class=candidate["impact_class"],
                        source_type=candidate["source_type"],
                        source_id=candidate["source_id"],
                        source_hash=candidate["source_hash"],
                        source_trust_class=candidate["source_trust_class"],
                        source_sequence=candidate["source_sequence"],
                        change_reason="candidate_accepted",
                        now=now,
                        valid_until=valid_until,
                    )
                    diff["added"].append(result["memory_id"])
                else:
                    revision = conn.execute(
                        "SELECT * FROM memory_revisions WHERE revision_id=?",
                        (active["current_revision_id"],),
                    ).fetchone()
                    if revision is not None and revision["content_hash"] == candidate_content_hash:
                        diff["retained"].append(active["memory_id"])
                    elif bool(active["pinned"]):
                        conn.execute(
                            "UPDATE memory_candidates SET status='proposed', rejection_code='MEMORY_PINNED_CONFLICT' WHERE candidate_id=?",
                            (candidate["candidate_id"],),
                        )
                        diff["needs_review"].append(candidate["candidate_id"])
                        continue
                    else:
                        revision_number = int(revision["revision_number"]) + 1
                        revision_id = f"memory_revision_{uuid4().hex}"
                        value = json.loads(candidate["content_json"])
                        scientific = (
                            candidate["impact_class"] == "scientific"
                            and candidate["kind"] == "project_decision"
                        )
                        conn.execute(
                            """
                            INSERT INTO memory_revisions
                                (revision_id, memory_id, revision_number, generation,
                                 content_json, content_text, content_hash, impact_class,
                                 confidence, sensitivity, confirmation_status,
                                 algorithm_id, algorithm_version, config_fingerprint,
                                 applicability_json, confirmation_event_id,
                                 change_reason, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                revision_id,
                                active["memory_id"],
                                revision_number,
                                active["generation"],
                                candidate["content_json"],
                                candidate["content_text"],
                                candidate_content_hash,
                                candidate["impact_class"],
                                candidate["confidence"],
                                candidate["sensitivity"],
                                "confirmed-project-decision" if scientific else None,
                                "1" if scientific else None,
                                (
                                    stable_hash(
                                        {
                                            "decision_kind": value.get("decision_kind"),
                                            "value": value.get("value"),
                                        }
                                    )
                                    if scientific
                                    else None
                                ),
                                canonical_json({"project_id": project_id}),
                                candidate["source_id"] if scientific else None,
                                "candidate_superseded_previous_revision",
                                now,
                            ),
                        )
                        conn.execute(
                            """
                            INSERT INTO memory_sources
                                (source_link_id, revision_id, memory_id, project_id,
                                 source_type, source_id, source_hash,
                                 source_trust_class, source_sequence, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                f"memory_source_{uuid4().hex}",
                                revision_id,
                                active["memory_id"],
                                project_id,
                                candidate["source_type"],
                                candidate["source_id"],
                                candidate["source_hash"],
                                candidate["source_trust_class"],
                                candidate["source_sequence"],
                                now,
                            ),
                        )
                        conn.execute(
                            "UPDATE memory_items SET current_revision_id=?, item_version=item_version+1, valid_until=?, updated_at=? WHERE memory_id=?",
                            (revision_id, valid_until, now, active["memory_id"]),
                        )
                        conn.execute(
                            "DELETE FROM memory_fts WHERE memory_id=?",
                            (active["memory_id"],),
                        )
                        conn.execute(
                            "INSERT INTO memory_fts(memory_id, project_id, revision_id, canonical_key, content_text) VALUES (?, ?, ?, ?, ?)",
                            (
                                active["memory_id"],
                                project_id,
                                revision_id,
                                candidate["canonical_key"],
                                candidate["content_text"],
                            ),
                        )
                        self._insert_event(
                            conn,
                            project_id=project_id,
                            principal="memory-consolidator",
                            event_type="superseded",
                            memory_id=active["memory_id"],
                            before_hash=revision["content_hash"] if revision else None,
                            after_hash=candidate_content_hash,
                            details={"candidate_id": candidate["candidate_id"]},
                            occurred_at=now,
                        )
                        diff["superseded"].append(active["memory_id"])
                conn.execute(
                    "UPDATE memory_candidates SET consolidated_at=? WHERE candidate_id=?",
                    (now, candidate["candidate_id"]),
                )
            output_hash = stable_hash(diff)
            conn.execute(
                """
                INSERT INTO memory_consolidation_runs
                    (consolidation_run_id, project_id, consent_epoch,
                     input_watermark, candidate_ids_json, selection_diff_json,
                     policy_version, output_hash, status, fencing_token,
                     created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'succeeded', ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    consent_epoch,
                    0,
                    canonical_json(candidate_ids),
                    canonical_json(diff),
                    policy_version,
                    output_hash,
                    fencing_token,
                    now,
                    now,
                ),
            )
            return {
                "status": "succeeded",
                "consolidation_run_id": run_id,
                "selection_diff": diff,
                "output_hash": output_hash,
            }

    def forget_item(
        self,
        *,
        project_id: str,
        memory_id: str,
        command_id: str,
        principal: str,
        expected_item_version: int,
        expected_revision_hash: str,
        ledger_record: dict[str, Any],
    ) -> dict[str, Any]:
        payload_hash = stable_hash(
            {
                "project_id": project_id,
                "memory_id": memory_id,
                "expected_item_version": expected_item_version,
                "expected_revision_hash": expected_revision_hash,
                "forget_epoch": ledger_record.get("forget_epoch"),
            }
        )
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            replay = self._command_result(
                conn,
                command_id=command_id,
                principal=principal,
                payload_hash=payload_hash,
            )
            if replay is not None:
                result = replay
            else:
                result = self._forget_item_in_transaction(
                    conn,
                    project_id=project_id,
                    memory_id=memory_id,
                    command_id=command_id,
                    principal=principal,
                    expected_item_version=expected_item_version,
                    expected_revision_hash=expected_revision_hash,
                    ledger_record=ledger_record,
                    payload_hash=payload_hash,
                    now=now,
                )
        self.ensure_forget_wal_truncated()
        return result

    def _forget_item_in_transaction(
        self,
        conn: sqlite3.Connection,
        *,
        project_id: str,
        memory_id: str,
        command_id: str,
        principal: str,
        expected_item_version: int,
        expected_revision_hash: str,
        ledger_record: dict[str, Any],
        payload_hash: str,
        now: str,
    ) -> dict[str, Any]:
        item = conn.execute(
            "SELECT * FROM memory_items WHERE project_id=? AND memory_id=?",
            (project_id, memory_id),
        ).fetchone()
        if item is None:
            raise MemoryRepositoryError("MEMORY_ITEM_NOT_FOUND")
        revision = conn.execute(
            "SELECT * FROM memory_revisions WHERE revision_id=? AND memory_id=?",
            (item["current_revision_id"], memory_id),
        ).fetchone()
        if (
            int(item["item_version"]) != expected_item_version
            or revision is None
            or revision["content_hash"] != expected_revision_hash
        ):
            raise MemoryRepositoryError("MEMORY_ITEM_STALE")
        if ledger_record.get("canonical_key") != item["canonical_key"]:
            raise MemoryRepositoryError("MEMORY_FORGET_LEDGER_MISMATCH")
        conn.execute(
            "UPDATE memory_revisions SET content_json='{}', content_text='' WHERE memory_id=?",
            (memory_id,),
        )
        conn.execute(
            """
            UPDATE memory_candidates SET content_json='{}', content_text='',
                status='suppressed', rejection_code='MEMORY_FORGOTTEN'
            WHERE project_id=? AND canonical_key=?
            """,
            (project_id, item["canonical_key"]),
        )
        conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (memory_id,))
        conn.execute(
            """
            UPDATE memory_items SET status='forgotten', pinned=0,
                item_version=item_version+1, updated_at=?
            WHERE project_id=? AND memory_id=?
            """,
            (now, project_id, memory_id),
        )
        tombstone_id = f"memory_tombstone_{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO memory_tombstones
                (tombstone_id, project_id, canonical_key, semantic_fingerprint,
                 source_lineage_fingerprints_json, content_hash,
                 key_schema_version, policy_version, generation, forget_epoch,
                 forget_outbox_sequence, principal, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tombstone_id,
                project_id,
                item["canonical_key"],
                ledger_record["semantic_fingerprint"],
                ledger_record["source_lineage_fingerprints_json"],
                revision["content_hash"],
                KEY_SCHEMA_VERSION,
                DEFAULT_POLICY_VERSION,
                int(item["generation"]),
                int(ledger_record["forget_epoch"]),
                int(ledger_record["forget_outbox_sequence"]),
                principal,
                now,
            ),
        )
        event_id = self._insert_event(
            conn,
            project_id=project_id,
            principal=principal,
            event_type="forgotten",
            command_id=command_id,
            memory_id=memory_id,
            before_hash=revision["content_hash"],
            details={
                "tombstone_id": tombstone_id,
                "forget_epoch": ledger_record["forget_epoch"],
            },
            occurred_at=now,
        )
        result = {
            "status": "forgotten",
            "memory_id": memory_id,
            "item_version": expected_item_version + 1,
            "tombstone_id": tombstone_id,
            "event_id": event_id,
        }
        self._record_command(
            conn,
            command_id=command_id,
            principal=principal,
            action="forget",
            project_id=project_id,
            target_id=memory_id,
            payload_hash=payload_hash,
            result=result,
            completed_at=now,
        )
        return result

    def ensure_forget_wal_truncated(self) -> None:
        """Require every committed forget to remove recoverable WAL frames."""

        try:
            with self._lock, self.connect(immediate=True) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO store_meta(key, value) VALUES('last_forget_wal_truncate_at', ?)",
                    (utc_iso(),),
                )
            with self._lock, self.connect() as conn:
                checkpoint = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error as exc:
            raise MemoryRepositoryError("MEMORY_FORGET_WAL_TRUNCATION_FAILED") from exc
        if checkpoint is None:
            raise MemoryRepositoryError("MEMORY_FORGET_WAL_TRUNCATION_FAILED")
        busy, remaining_frames, _checkpointed_frames = (int(value) for value in checkpoint)
        if busy != 0 or remaining_frames != 0:
            raise MemoryRepositoryError("MEMORY_FORGET_WAL_TRUNCATION_FAILED")

    def restore_item(
        self,
        *,
        project_id: str,
        memory_id: str,
        command_id: str,
        principal: str,
        expected_item_version: int,
        expected_revision_hash: str,
        value: dict[str, Any],
        summary: str,
    ) -> dict[str, Any]:
        payload_hash = stable_hash(
            {
                "project_id": project_id,
                "memory_id": memory_id,
                "expected_item_version": expected_item_version,
                "expected_revision_hash": expected_revision_hash,
                "value": value,
                "summary": summary,
            }
        )
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            replay = self._command_result(
                conn,
                command_id=command_id,
                principal=principal,
                payload_hash=payload_hash,
            )
            if replay is not None:
                return replay
            item = conn.execute(
                "SELECT * FROM memory_items WHERE project_id=? AND memory_id=?",
                (project_id, memory_id),
            ).fetchone()
            if item is None or item["status"] != "forgotten":
                raise MemoryRepositoryError("MEMORY_ITEM_NOT_RESTORABLE")
            previous = conn.execute(
                "SELECT * FROM memory_revisions WHERE revision_id=?",
                (item["current_revision_id"],),
            ).fetchone()
            if (
                int(item["item_version"]) != expected_item_version
                or previous is None
                or previous["content_hash"] != expected_revision_hash
            ):
                raise MemoryRepositoryError("MEMORY_ITEM_STALE")
            tombstone = conn.execute(
                """
                SELECT * FROM memory_tombstones
                WHERE project_id=? AND canonical_key=?
                ORDER BY generation DESC, created_at DESC LIMIT 1
                """,
                (project_id, item["canonical_key"]),
            ).fetchone()
            generation = max(
                int(item["generation"]) + 1,
                (int(tombstone["generation"]) + 1) if tombstone else 1,
            )
            revision_number = int(previous["revision_number"]) + 1
            revision_id = f"memory_revision_{uuid4().hex}"
            content_hash = stable_hash({"value": value, "summary": summary})
            conn.execute(
                """
                INSERT INTO memory_revisions
                    (revision_id, memory_id, revision_number, generation,
                     content_json, content_text, content_hash, impact_class,
                     confidence, sensitivity, confirmation_status,
                     applicability_json, change_reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'project_internal',
                        'confirmed', ?, 'explicit_restore', ?)
                """,
                (
                    revision_id,
                    memory_id,
                    revision_number,
                    generation,
                    canonical_json(value),
                    summary,
                    content_hash,
                    previous["impact_class"],
                    canonical_json({"project_id": project_id}),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO memory_sources
                    (source_link_id, revision_id, memory_id, project_id,
                     source_type, source_id, source_hash, source_trust_class,
                     source_sequence, created_at)
                VALUES (?, ?, ?, ?, 'explicit_restore', ?, ?, 'explicit_user', NULL, ?)
                """,
                (
                    f"memory_source_{uuid4().hex}",
                    revision_id,
                    memory_id,
                    project_id,
                    command_id,
                    payload_hash,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE memory_items SET current_revision_id=?, status='active',
                    generation=?, item_version=item_version+1, updated_at=?
                WHERE project_id=? AND memory_id=?
                """,
                (revision_id, generation, now, project_id, memory_id),
            )
            conn.execute(
                "INSERT INTO memory_fts(memory_id, project_id, revision_id, canonical_key, content_text) VALUES (?, ?, ?, ?, ?)",
                (memory_id, project_id, revision_id, item["canonical_key"], summary),
            )
            event_id = self._insert_event(
                conn,
                project_id=project_id,
                principal=principal,
                event_type="restored",
                command_id=command_id,
                memory_id=memory_id,
                before_hash=previous["content_hash"],
                after_hash=content_hash,
                details={
                    "generation": generation,
                    "restored_from_tombstone_id": (
                        tombstone["tombstone_id"] if tombstone else None
                    ),
                },
                occurred_at=now,
            )
            result = {
                "status": "active",
                "memory_id": memory_id,
                "revision_id": revision_id,
                "revision_hash": content_hash,
                "item_version": expected_item_version + 1,
                "generation": generation,
                "event_id": event_id,
            }
            self._record_command(
                conn,
                command_id=command_id,
                principal=principal,
                action="restore",
                project_id=project_id,
                target_id=memory_id,
                payload_hash=payload_hash,
                result=result,
                completed_at=now,
            )
            return result

    def expire_due_items(self, *, project_id: str) -> int:
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            rows = conn.execute(
                """
                SELECT memory_id, current_revision_id FROM memory_items
                WHERE project_id=? AND status='active' AND pinned=0
                  AND valid_until IS NOT NULL AND valid_until<=?
                """,
                (project_id, now),
            ).fetchall()
            for row in rows:
                revision = conn.execute(
                    "SELECT content_hash FROM memory_revisions WHERE revision_id=?",
                    (row["current_revision_id"],),
                ).fetchone()
                conn.execute(
                    "UPDATE memory_items SET status='expired', item_version=item_version+1, updated_at=? WHERE memory_id=?",
                    (now, row["memory_id"]),
                )
                conn.execute("DELETE FROM memory_fts WHERE memory_id=?", (row["memory_id"],))
                self._insert_event(
                    conn,
                    project_id=project_id,
                    principal="memory-maintenance",
                    event_type="expired",
                    memory_id=row["memory_id"],
                    before_hash=revision["content_hash"] if revision else None,
                    occurred_at=now,
                )
            return len(rows)

    def expire_due_candidates(self, *, project_id: str) -> int:
        now = utc_iso()
        with self._lock, self.connect(immediate=True) as conn:
            rows = conn.execute(
                """
                SELECT candidate_id, candidate_hash FROM memory_candidates
                WHERE project_id=? AND status='proposed'
                  AND expires_at IS NOT NULL AND expires_at<=?
                """,
                (project_id, now),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE memory_candidates
                    SET content_json='{}', content_text='', status='expired',
                        rejection_code='MEMORY_CANDIDATE_EXPIRED', candidate_version=candidate_version+1
                    WHERE candidate_id=?
                    """,
                    (row["candidate_id"],),
                )
                self._insert_event(
                    conn,
                    project_id=project_id,
                    principal="memory-maintenance",
                    event_type="candidate_expired",
                    candidate_id=row["candidate_id"],
                    before_hash=row["candidate_hash"],
                    occurred_at=now,
                )
            return len(rows)

    def scrub_stale_candidates(
        self, *, project_id: str, current_consent_epoch: int
    ) -> int:
        with self._lock, self.connect(immediate=True) as conn:
            cursor = conn.execute(
                """
                UPDATE memory_candidates
                SET content_json='{}', content_text='', status='suppressed',
                    rejection_code='MEMORY_CONSENT_EPOCH_STALE'
                WHERE project_id=? AND consent_epoch<?
                  AND status IN ('proposed', 'accepted')
                """,
                (project_id, current_consent_epoch),
            )
            return int(cursor.rowcount)

    def release_expired_leases(self) -> int:
        with self._lock, self.connect(immediate=True) as conn:
            cursor = conn.execute(
                "DELETE FROM memory_leases WHERE lease_expires_at<=strftime('%Y-%m-%dT%H:%M:%SZ','now')"
            )
            return int(cursor.rowcount)

    def vacuum(self) -> None:
        self.ensure_forget_wal_truncated()
        with self.connect() as conn:
            conn.execute("VACUUM")

    def projection_revision(self, *, project_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(event_sequence), 0) AS value FROM memory_events WHERE project_id=?",
                (project_id,),
            ).fetchone()
        return int(row["value"])

    def record_projection_rebuilt(
        self,
        *,
        project_id: str,
        manifest_hash: str,
        file_count: int,
    ) -> str:
        with self._lock, self.connect(immediate=True) as conn:
            return self._insert_event(
                conn,
                project_id=project_id,
                principal="memory-projection",
                event_type="projection_rebuilt",
                after_hash=manifest_hash,
                details={"file_count": file_count},
            )
