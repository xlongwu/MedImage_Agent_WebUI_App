from __future__ import annotations

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.sandbox import SandboxPolicySet
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
