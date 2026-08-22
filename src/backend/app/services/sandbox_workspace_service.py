"""Prepare deterministic, project-contained workspaces for sandbox attempts."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.sandbox import SandboxAttemptRecord, SandboxPolicy


logger = logging.getLogger(__name__)


class SandboxAttemptStore(Protocol):
    def add_sandbox_attempt(self, attempt: SandboxAttemptRecord) -> SandboxAttemptRecord: ...
    def get_sandbox_attempt(self, sandbox_id: str) -> SandboxAttemptRecord | None: ...
    def update_sandbox_attempt(self, sandbox_id: str, **updates: object) -> SandboxAttemptRecord | None: ...


def _safe_id(value: str, label: str) -> str:
    if not value or value in {".", ".."} or any(token in value for token in ("/", "\\", ":", "..")):
        raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT", details={"field": label})
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class SandboxWorkspaceService:
    def __init__(self, store: SandboxAttemptStore) -> None:
        self.store = store

    def prepare(
        self,
        *,
        project_id: str,
        run_id: str,
        node_id: str,
        subject_id: str | None,
        execution_ticket_id: str,
        dispatch_id: str,
        policy: SandboxPolicy,
        project_work_root: str | Path,
        approved_project_root: str | Path,
    ) -> SandboxAttemptRecord:
        _safe_id(run_id, "run_id")
        _safe_id(node_id, "node_id")
        if subject_id is not None:
            _safe_id(subject_id, "subject_id")
        root = Path(project_work_root).expanduser().resolve()
        project_root = Path(approved_project_root).expanduser().resolve()
        if root.name.casefold() == "rawdata" or not _within(root, project_root):
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        if root.exists() and root.is_symlink():
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        root.mkdir(parents=True, exist_ok=True)
        attempt_id = _digest("|".join((dispatch_id, node_id, subject_id or "project")))
        sandbox_id = _digest("|".join((execution_ticket_id, run_id, node_id, subject_id or "project", attempt_id)))
        directory = (root / "sandboxes" / run_id / node_id / attempt_id).resolve()
        if not _within(directory, root):
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        record = SandboxAttemptRecord(
            sandbox_id=sandbox_id, project_id=project_id, run_id=run_id, node_id=node_id,
            subject_id=subject_id, attempt_id=attempt_id,
            execution_ticket_id=execution_ticket_id, dispatch_id=dispatch_id,
            policy_hash=policy.policy_hash, status="PREPARING",
            owner_pid=os.getpid(),
        )
        try:
            existing = self.store.add_sandbox_attempt(record)
        except Exception as exc:
            raise StateStoreError("SANDBOX_ATTEMPT_WRITE_FAILED") from exc
        if existing.status != "PREPARING":
            if existing.policy_hash != policy.policy_hash:
                raise SafetyError("SANDBOX_IDENTITY_CONFLICT", code="SANDBOX_IDENTITY_CONFLICT")
            return existing
        for name in ("staged_input", "output", "logs", "tmp", "meta"):
            (directory / name).mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / "meta" / "policy.json", policy.model_dump(mode="json"), schema_version=1)
        atomic_write_json(directory / "meta" / "result.json", {"sandbox_id": sandbox_id, "status": "PREPARED"}, schema_version=1)
        updated = self.store.update_sandbox_attempt(sandbox_id, status="PREPARED")
        if updated is None:
            raise StateStoreError("SANDBOX_ATTEMPT_WRITE_FAILED")
        logger.info(
            "sandbox_prepared",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                execution_ticket_id=execution_ticket_id,
                run_id=run_id,
                sandbox_id=sandbox_id,
                event_code="SANDBOX_PREPARED",
            )},
        )
        return updated

    def stage_inputs(
        self,
        *,
        attempt: SandboxAttemptRecord,
        project_work_root: str | Path,
        inputs: tuple[str | Path, ...],
        approved_input_roots: tuple[str | Path, ...],
    ) -> tuple[dict[str, object], ...]:
        root = Path(project_work_root).expanduser().resolve()
        directory = (root / "sandboxes" / attempt.run_id / attempt.node_id / attempt.attempt_id).resolve()
        if not _within(directory, root):
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        staged = directory / "staged_input"
        manifest: list[dict[str, object]] = []
        for source_value in inputs:
            source = Path(source_value).expanduser().resolve()
            if (
                not source.is_file()
                or source.is_symlink()
                or not any(_within(source, Path(value).expanduser().resolve()) for value in approved_input_roots)
            ):
                raise SafetyError("SANDBOX_INPUT_NOT_APPROVED", code="SANDBOX_INPUT_NOT_APPROVED")
            destination = staged / source.name
            if destination.exists():
                raise SafetyError("SANDBOX_IDENTITY_CONFLICT", code="SANDBOX_IDENTITY_CONFLICT")
            before = self._sha256(source)
            shutil.copy2(source, destination)
            after = self._sha256(destination)
            if before != after or source.stat().st_size != destination.stat().st_size:
                destination.unlink(missing_ok=True)
                raise SafetyError("SANDBOX_INPUT_COPY_MISMATCH", code="SANDBOX_INPUT_COPY_MISMATCH")
            manifest.append({"name": source.name, "sha256": after, "size_bytes": destination.stat().st_size})
        atomic_write_json(directory / "meta" / "input_manifest.json", {"inputs": manifest}, schema_version=1)
        return tuple(manifest)

    def mark_running(self, attempt: SandboxAttemptRecord) -> SandboxAttemptRecord:
        updated = self.store.update_sandbox_attempt(attempt.sandbox_id, status="RUNNING", started_at=datetime.now(UTC))
        if updated is None:
            raise StateStoreError("SANDBOX_ATTEMPT_WRITE_FAILED")
        logger.info(
            "sandbox_running",
            extra={"medimage": agent_log_context(
                project_id=attempt.project_id,
                execution_ticket_id=attempt.execution_ticket_id,
                run_id=attempt.run_id,
                sandbox_id=attempt.sandbox_id,
                event_code="SANDBOX_RUNNING",
            )},
        )
        return updated

    def finalize(self, attempt: SandboxAttemptRecord, *, status: str, result_code: str | None = None, output_manifest_hash: str | None = None, output_count: int = 0) -> SandboxAttemptRecord:
        updated = self.store.update_sandbox_attempt(
            attempt.sandbox_id, status=status, result_code=result_code, output_manifest_hash=output_manifest_hash,
            output_count=output_count, ended_at=datetime.now(UTC),
        )
        if updated is None:
            raise StateStoreError("SANDBOX_ATTEMPT_WRITE_FAILED")
        logger.info(
            "sandbox_terminal",
            extra={"medimage": agent_log_context(
                project_id=attempt.project_id,
                execution_ticket_id=attempt.execution_ticket_id,
                run_id=attempt.run_id,
                sandbox_id=attempt.sandbox_id,
                event_code=f"SANDBOX_{status}",
            )},
        )
        return updated

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
