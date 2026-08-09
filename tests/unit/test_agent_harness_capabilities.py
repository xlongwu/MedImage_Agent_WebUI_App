from __future__ import annotations

import pytest

from src.backend.app.runtime.agent_capability_catalog import (
    AGENT_CAPABILITY_CATALOG,
    assert_capability_allowed,
    assert_capability_context_and_output_allowed,
)


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
    capability = assert_capability_allowed(kind, state)
    assert capability.automation_level in {"A0", "A1"}
    assert capability.requires_current_approval is False
    assert capability.side_effect_class in {"read_only", "managed_state"}
    assert capability.allowed_context_sections
    assert capability.allowed_output_types


def test_unknown_or_wrong_state_capabilities_fail_closed() -> None:
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_allowed("execute", "CREATED")
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_allowed("draft_plan", "WAITING_FOR_APPROVAL")


def test_catalog_never_exposes_a2_a4_or_current_approval_to_the_model() -> None:
    assert set(AGENT_CAPABILITY_CATALOG) == {
        "read_evidence", "request_decision", "draft_plan", "explain_result",
        "propose_recovery", "finish",
    }
    assert AGENT_CAPABILITY_CATALOG["read_evidence"].automation_level == "A0"
    assert all(
        capability.automation_level in {"A0", "A1"}
        and capability.requires_current_approval is False
        for capability in AGENT_CAPABILITY_CATALOG.values()
    )


def test_catalog_rejects_undeclared_context_or_output_authority() -> None:
    capability = assert_capability_allowed("finish", "CREATED")
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_context_and_output_allowed(
            capability,
            context_sections={"execution_state"},
            output_type="attempt_finished",
        )
    with pytest.raises(ValueError, match="AGENT_HARNESS_CAPABILITY_DENIED"):
        assert_capability_context_and_output_allowed(
            capability,
            context_sections=set(),
            output_type="execution_ticket",
        )
