from __future__ import annotations

import pytest

from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed


@pytest.mark.parametrize(
    ("kind", "state"),
    [
        ("read_evidence", "CREATED"),
        ("request_decision", "CONTEXT_READY"),
        ("draft_plan", "PLAN_DRAFTED"),
        ("explain_result", "SUCCEEDED"),
        ("propose_recovery", "DIAGNOSING"),
        ("finish", "WAITING_FOR_APPROVAL"),
    ],
)
def test_only_six_catalog_actions_are_available_in_their_explicit_states(kind, state) -> None:
    assert assert_capability_allowed(kind, state).read_only is True


def test_unknown_or_wrong_state_capabilities_fail_closed() -> None:
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_allowed("execute", "CREATED")
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_allowed("draft_plan", "WAITING_FOR_APPROVAL")
