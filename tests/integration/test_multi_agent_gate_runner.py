from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.app.schemas.agent_eval import MultiAgentEvalManifest, MultiAgentGateArmObservation
from src.backend.app.services.multi_agent_gate_runner import GateArmExecution, MultiAgentGateRunner
from tests.unit.test_multi_agent_evaluation import _manifest_payload


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class _Executor:
    def execute(self, *, case, arm, repetition, workspace: Path, gate_run_id: str) -> GateArmExecution:
        assert workspace.exists()
        return GateArmExecution(
            observation=MultiAgentGateArmObservation(
                case_id=case.case_id, arm=arm, repetition=repetition, status="safe_stop", conclusion_hash=_hash(f"{case.case_id}:{arm}"),
                lifecycle_id_hash=_hash(f"{gate_run_id}:{case.case_id}:{arm}:{repetition}"), team_worker_started=arm == "candidate" and case.team_eligible,
                safety_reviewer_completed=True if arm == "candidate" and case.team_eligible else None, elapsed_ms=1,
            ),
            model_calls=(),
        )


def test_runner_requires_explicit_network_and_writes_one_append_only_bundle(tmp_path: Path) -> None:
    manifest = MultiAgentEvalManifest.model_validate(_manifest_payload())
    runner = MultiAgentGateRunner()
    with pytest.raises(ValueError, match="NETWORK_NOT_ALLOWED"):
        runner.run(manifest=manifest, source_tree_hash=_hash("tree"), executor=_Executor(), allow_network=False, provider_approved=True)

    bundle = runner.run(manifest=manifest, source_tree_hash=_hash("tree"), executor=_Executor(), allow_network=True, provider_approved=True)
    target = runner.append_bundle(bundle=bundle, output_root=tmp_path)

    assert target.suffix == ".jsonl"
    assert json.loads(target.read_text(encoding="utf-8"))["manifest_hash"] == bundle.manifest_hash
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        runner.append_bundle(bundle=bundle, output_root=tmp_path)
