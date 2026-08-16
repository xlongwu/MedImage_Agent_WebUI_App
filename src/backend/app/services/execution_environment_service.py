"""Capture and re-verify only the local environment facts used by a plan."""

from __future__ import annotations

import platform as platform_module
import shutil
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.desktop_config import get_desktop_config
from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS
from src.backend.app.schemas.execution_environment import (
    BackendCapabilitySnapshot,
    ExecutionEnvironmentSnapshot,
    ToolCapabilitySnapshot,
)
from src.backend.app.version import APP_VERSION


class ExecutionEnvironmentStore(Protocol):
    def add_execution_environment_snapshot(
        self, snapshot: ExecutionEnvironmentSnapshot
    ) -> ExecutionEnvironmentSnapshot: ...

    def get_execution_environment_snapshot(
        self, snapshot_id: str
    ) -> ExecutionEnvironmentSnapshot | None: ...


def _path_hash(value: str | Path | None) -> str | None:
    if not value:
        return None
    return stable_hash({"resolved_path": str(Path(value).expanduser().resolve())})


def _root_hash(roots: tuple[str, ...]) -> str:
    return stable_hash(tuple(sorted(set(roots))))


class ExecutionEnvironmentService:
    """A deterministic adapter over existing local environment probes."""

    def __init__(
        self,
        store: ExecutionEnvironmentStore | None = None,
        *,
        config_reader: Callable[[], dict[str, Any]] = get_desktop_config,
        gpu_detector: Callable[[], dict[str, Any]] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.config_reader = config_reader
        self.gpu_detector = gpu_detector
        self.now = now or (lambda: datetime.now(UTC))

    def capture_for_plan(
        self,
        *,
        project_id: str,
        reviewed_plan,
        write_roots: tuple[str, ...],
        readonly_roots: tuple[str, ...],
        persist: bool = True,
    ) -> ExecutionEnvironmentSnapshot:
        node_ids, backend_ids = self._plan_scope(reviewed_plan)
        snapshot = self._capture(
            project_id=project_id,
            node_ids=node_ids,
            backend_ids=backend_ids,
            write_roots=write_roots,
            readonly_roots=readonly_roots,
        )
        if persist and self.store is not None:
            try:
                return self.store.add_execution_environment_snapshot(snapshot)
            except Exception as exc:
                raise StateStoreError("EXECUTION_ENVIRONMENT_SNAPSHOT_WRITE_FAILED") from exc
        return snapshot

    def verify_for_dispatch(self, *, execution_ticket) -> ExecutionEnvironmentSnapshot:
        if self.store is None:
            raise SafetyError(
                "EXECUTION_ENVIRONMENT_STORE_REQUIRED",
                code="EXECUTION_ENVIRONMENT_STORE_REQUIRED",
            )
        expected = self.store.get_execution_environment_snapshot(
            execution_ticket.execution_environment_snapshot_id
        )
        if expected is None or expected.project_id != execution_ticket.project_id:
            raise SafetyError(
                "EXECUTION_ENVIRONMENT_SNAPSHOT_MISSING",
                code="EXECUTION_ENVIRONMENT_SNAPSHOT_MISSING",
            )
        if expected.environment_hash != execution_ticket.execution_environment_hash:
            raise SafetyError(
                "EXECUTION_ENVIRONMENT_CHANGED",
                code="EXECUTION_ENVIRONMENT_CHANGED",
            )
        current = self._capture(
            project_id=execution_ticket.project_id,
            node_ids=execution_ticket.approved_node_ids,
            backend_ids=execution_ticket.approved_backend_ids,
            write_roots_hash=expected.write_roots_hash,
            readonly_roots_hash=expected.readonly_roots_hash,
        )
        if current.environment_hash != expected.environment_hash:
            raise SafetyError(
                "EXECUTION_ENVIRONMENT_CHANGED",
                code="EXECUTION_ENVIRONMENT_CHANGED",
            )
        return expected

    def _capture(
        self,
        *,
        project_id: str,
        node_ids: tuple[str, ...],
        backend_ids: tuple[str, ...],
        write_roots: tuple[str, ...] | None = None,
        readonly_roots: tuple[str, ...] | None = None,
        write_roots_hash: str | None = None,
        readonly_roots_hash: str | None = None,
    ) -> ExecutionEnvironmentSnapshot:
        config = self.config_reader()
        tool_capabilities = self._tool_capabilities(backend_ids, config)
        backend_capabilities = self._backend_capabilities(
            backend_ids, config, tool_capabilities
        )
        contract_versions = tuple(
            sorted((node_id, NODE_CONTRACTS[node_id].contract_version) for node_id in node_ids)
        )
        identity = {
            "schema_version": 2,
            "project_id": project_id,
            "provider_kind": "local",
            "platform": platform_module.platform(),
            "python_version": sys.version,
            "app_version": APP_VERSION,
            "node_registry_hash": stable_hash(
                [NODE_CONTRACTS[node_id].model_dump(mode="json") for node_id in node_ids]
            ),
            "contract_versions": contract_versions,
            "tool_capabilities": [item.model_dump(mode="json") for item in tool_capabilities],
            "backend_capabilities": [item.model_dump(mode="json") for item in backend_capabilities],
            "write_roots_hash": write_roots_hash or _root_hash(write_roots or ()),
            "readonly_roots_hash": readonly_roots_hash or _root_hash(readonly_roots or ()),
            "sandbox_provider": "windows_restricted_process",
            "sandbox_provider_version": "windows-sandbox-v1",
            "sandbox_runtime_hash": stable_hash({
                "provider": "windows_restricted_process",
                "version": "windows-sandbox-v1",
                "process_mode": "CreateRestrictedToken+JobObject",
                "network_isolation": "not_enforced",
            }),
        }
        environment_hash = stable_hash(identity)
        return ExecutionEnvironmentSnapshot(
            snapshot_id=f"environment_{uuid4().hex}",
            captured_at=self.now(),
            environment_hash=environment_hash,
            **identity,
        )

    @staticmethod
    def _plan_scope(reviewed_plan) -> tuple[tuple[str, ...], tuple[str, ...]]:
        payload = getattr(reviewed_plan, "payload", {})
        plan = payload.get("plan") if isinstance(payload, dict) else None
        nodes = plan.get("nodes") if isinstance(plan, dict) else None
        if not isinstance(nodes, list):
            raise SafetyError("EXECUTION_ENVIRONMENT_PLAN_INVALID", code="EXECUTION_ENVIRONMENT_PLAN_INVALID")
        node_ids = tuple(sorted({str(node.get("id") or "") for node in nodes if isinstance(node, dict)} - {""}))
        if not node_ids or any(node_id not in NODE_CONTRACTS for node_id in node_ids):
            raise SafetyError("EXECUTION_ENVIRONMENT_PLAN_INVALID", code="EXECUTION_ENVIRONMENT_PLAN_INVALID")
        backend_ids = tuple(sorted({NODE_CONTRACTS[node_id].backend for node_id in node_ids}))
        return node_ids, backend_ids

    def _tool_capabilities(
        self, backend_ids: tuple[str, ...], config: dict[str, Any]
    ) -> tuple[ToolCapabilitySnapshot, ...]:
        needed: set[str] = set()
        if any(backend in {"matlab-spm", "dpabi", "matlab-dpabi"} for backend in backend_ids):
            needed.add("matlab")
        if "matlab-spm" in backend_ids:
            needed.add("spm")
        if any(backend in {"dpabi", "matlab-dpabi"} for backend in backend_ids):
            needed.add("dpabi")
        return tuple(self._tool_capability(tool_id, config) for tool_id in sorted(needed))

    @staticmethod
    def _tool_capability(tool_id: str, config: dict[str, Any]) -> ToolCapabilitySnapshot:
        key = {"matlab": "matlab_command", "spm": "spm_dir", "dpabi": "dpabi_dir"}[tool_id]
        configured = str(config.get(key) or "")
        resolved = shutil.which(configured) if tool_id == "matlab" else None
        exists = bool(resolved) if tool_id == "matlab" else Path(configured).expanduser().exists()
        status = "available" if exists else "unavailable"
        version = None
        if tool_id in {"spm", "dpabi"} and exists:
            version = Path(configured).expanduser().resolve().name
        return ToolCapabilitySnapshot(
            tool_id=tool_id,
            status=status,
            version=version,
            installation_path_hash=_path_hash(resolved or configured),
            configuration_hash=stable_hash({key: configured}),
            error_codes=() if exists else (f"{tool_id.upper()}_UNAVAILABLE",),
        )

    def _backend_capabilities(
        self,
        backend_ids: tuple[str, ...],
        config: dict[str, Any],
        tools: tuple[ToolCapabilitySnapshot, ...],
    ) -> tuple[BackendCapabilitySnapshot, ...]:
        tool_status = {item.tool_id: item.status for item in tools}
        result: list[BackendCapabilitySnapshot] = []
        for backend_id in backend_ids:
            if backend_id == "gpu":
                gpu = self._gpu_capability()
                result.append(gpu)
                continue
            if backend_id in {"matlab-spm", "dpabi", "matlab-dpabi"}:
                required = ("matlab", "spm") if backend_id == "matlab-spm" else ("matlab", "dpabi")
                available = all(tool_status.get(tool) == "available" for tool in required)
                result.append(BackendCapabilitySnapshot(
                    backend_id=backend_id,
                    status="disabled" if available else "unavailable",
                    configuration_hash=stable_hash({"backend": backend_id, "tools": required}),
                    error_codes=() if available else ("EXTERNAL_BACKEND_UNAVAILABLE",),
                    warnings=("External execution remains disabled by policy.",) if available else (),
                ))
                continue
            result.append(BackendCapabilitySnapshot(
                backend_id=backend_id,
                status="available",
                version=sys.version,
                executable_path_hash=_path_hash(sys.executable),
                configuration_hash=stable_hash({"backend": backend_id, "python": sys.executable}),
            ))
        return tuple(result)

    def _gpu_capability(self) -> BackendCapabilitySnapshot:
        detector = self.gpu_detector
        if detector is None:
            from src.backend.app.tools.gpu_utils import detect_gpu

            detector = detect_gpu
        gpu = detector()
        available = bool(gpu.get("gpu_available"))
        code = gpu.get("capability_error_code")
        return BackendCapabilitySnapshot(
            backend_id="gpu",
            status="available" if available else "unavailable",
            version=(
                f"cuda-{gpu.get('cuda_runtime_version')}"
                if gpu.get("cuda_runtime_version") is not None
                else None
            ),
            configuration_hash=stable_hash({
                "gpu_mode": str(self.config_reader().get("gpu_mode") or ""),
                "device_id": gpu.get("device_id"),
                "device_count": gpu.get("device_count"),
            }),
            error_codes=(str(code),) if code else (),
            warnings=tuple(str(item) for item in gpu.get("warnings", []) if isinstance(item, str)),
        )
