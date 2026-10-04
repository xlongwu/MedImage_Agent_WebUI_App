from __future__ import annotations

import pytest
import importlib.util
from types import SimpleNamespace

from pydantic import ValidationError

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxPolicySet
from src.backend.app.schemas.sandbox import SandboxAttemptRecord
from src.backend.app.runtime.sandbox_process_runner import sandbox_runtime_fingerprint
from src.backend.app.services.sandbox_policy_service import SandboxPolicyService, empty_policy_set


def test_empty_sandbox_policy_set_is_stable_and_verifiable() -> None:
    first = empty_policy_set()
    second = empty_policy_set()
    assert first.policies_hash == second.policies_hash
    SandboxPolicyService.verify(first)


def test_modified_policy_set_is_rejected_before_dispatch() -> None:
    policy_set = empty_policy_set().model_copy(update={"policies_hash": "changed"})
    with pytest.raises(SafetyError, match="EXECUTION_SANDBOX_POLICY_CHANGED"):
        SandboxPolicyService.verify(policy_set)


def test_runtime_fingerprint_tracks_actual_source_and_frozen_code(tmp_path, monkeypatch) -> None:
    source = tmp_path / "provider.py"
    source.write_text("deny_network = True", encoding="utf-8")
    spec = SimpleNamespace(origin=str(source), loader=object())
    monkeypatch.setattr(importlib.util, "find_spec", lambda _: spec)
    initial = sandbox_runtime_fingerprint()
    assert len(initial) == 64
    assert sandbox_runtime_fingerprint() == initial
    source.write_text("deny_network = False", encoding="utf-8")
    assert sandbox_runtime_fingerprint() != initial
    frozen_code = compile("deny_network = True", "frozen/provider.py", "exec")
    spec = SimpleNamespace(origin=None, loader=SimpleNamespace(get_code=lambda _: frozen_code))
    frozen = sandbox_runtime_fingerprint()
    frozen_code = compile("deny_network = False", "frozen/provider.py", "exec")
    assert sandbox_runtime_fingerprint() != frozen
    spec = SimpleNamespace(origin=None, loader=object())
    with pytest.raises(SafetyError, match="SANDBOX_PROVIDER_FINGERPRINT_UNAVAILABLE"):
        sandbox_runtime_fingerprint()


def test_attempt_rejects_removed_network_status_and_old_schema() -> None:
    identity = dict(
        sandbox_id="sandbox", project_id="project", run_id="run", node_id="node",
        attempt_id="attempt", execution_ticket_id="ticket", dispatch_id="dispatch",
        policy_hash="policy", status="PREPARED",
    )
    assert SandboxAttemptRecord(**identity).network_isolation == "unverified"
    for obsolete in ({"network_isolation": "not_enforced"}, {"schema_version": 2}, {"policy_version": "windows-sandbox-v1"}):
        with pytest.raises(ValidationError):
            SandboxAttemptRecord(**identity, **obsolete)
