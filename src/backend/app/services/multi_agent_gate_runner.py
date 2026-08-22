"""Isolated G0 runner orchestration and append-only run-bundle persistence.

The runner owns neither a production project nor a provider implementation.  A
caller supplies the lifecycle/Harness executor through this narrow protocol;
that makes the temporary SQLite/workspace boundary explicit and testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol
from uuid import uuid4

from src.backend.app.schemas.agent_eval import MultiAgentEvalCase, MultiAgentEvalManifest, MultiAgentGateArmObservation, MultiAgentGateModelCallRecord, MultiAgentGateRunBundle
from src.backend.app.services.multi_agent_evaluation_service import MultiAgentEvaluationService, canonical_hash


@dataclass(frozen=True)
class GateArmExecution:
    observation: MultiAgentGateArmObservation
    model_calls: tuple[MultiAgentGateModelCallRecord, ...]


class IsolatedGateArmExecutor(Protocol):
    """Execute the real lifecycle/Harness only inside ``workspace``."""

    def execute(self, *, case: MultiAgentEvalCase, arm: str, repetition: int, workspace: Path, gate_run_id: str) -> GateArmExecution: ...


class MultiAgentGateRunner:
    """Run each arm in a fresh temporary workspace and emit no production writes."""

    RUNNER_VERSION = "multi-agent-gate-runner-v1"

    def run(self, *, manifest: MultiAgentEvalManifest, source_tree_hash: str, executor: IsolatedGateArmExecutor, allow_network: bool, provider_approved: bool) -> MultiAgentGateRunBundle:
        if not allow_network:
            raise ValueError("MULTI_AGENT_GATE_NETWORK_NOT_ALLOWED")
        if not provider_approved:
            raise ValueError("MULTI_AGENT_GATE_PROVIDER_NOT_APPROVED")
        if manifest.runner_version != self.RUNNER_VERSION:
            raise ValueError("MULTI_AGENT_GATE_RUNNER_VERSION_MISMATCH")
        gate_run_id = f"g0-{uuid4()}"
        calls: list[MultiAgentGateModelCallRecord] = []
        observations: list[MultiAgentGateArmObservation] = []
        for case in manifest.cases:
            for arm in ("baseline", "candidate"):
                for repetition in (1, 2):
                    with TemporaryDirectory(prefix="medimage-multi-agent-g0-") as root:
                        execution = executor.execute(case=case, arm=arm, repetition=repetition, workspace=Path(root), gate_run_id=gate_run_id)
                    if execution.observation.case_id != case.case_id or execution.observation.arm != arm or execution.observation.repetition != repetition:
                        raise ValueError("MULTI_AGENT_GATE_EXECUTOR_OBSERVATION_SCOPE_INVALID")
                    if any(call.case_id != case.case_id or call.arm != arm or call.repetition != repetition for call in execution.model_calls):
                        raise ValueError("MULTI_AGENT_GATE_EXECUTOR_CALL_SCOPE_INVALID")
                    observations.append(execution.observation)
                    calls.extend(execution.model_calls)
        return MultiAgentGateRunBundle(
            gate_run_id=gate_run_id,
            manifest_hash=MultiAgentEvaluationService().manifest_hash(manifest),
            source_revision=manifest.source_revision,
            source_tree_hash=source_tree_hash,
            runner_version=self.RUNNER_VERSION,
            provider_id=manifest.provider_id,
            model_id=manifest.model_id,
            model_profile_hash=manifest.model_profile_hash,
            role_registry_hash=manifest.role_registry_hash,
            context_projector_hash=manifest.context_projector_hash,
            aggregation_policy_hash=manifest.aggregation_policy_hash,
            model_calls=tuple(calls), observations=tuple(observations),
        )

    @staticmethod
    def append_bundle(*, bundle: MultiAgentGateRunBundle, output_root: Path) -> Path:
        """Write a single immutable JSONL event; never overwrite an existing bundle."""
        output_root = output_root.resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        target = output_root / f"{bundle.gate_run_id}.jsonl"
        if target.exists():
            raise ValueError("MULTI_AGENT_GATE_RUN_BUNDLE_ALREADY_EXISTS")
        payload = bundle.model_dump(mode="json")
        payload["bundle_hash"] = canonical_hash(payload)
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return target
