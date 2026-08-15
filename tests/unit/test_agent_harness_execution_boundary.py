from __future__ import annotations

import pytest

from src.backend.app.schemas.agent_harness import action_envelope_json_schema, parse_action_envelope


@pytest.mark.parametrize("kind", ["execute", "approve", "read_evidence", "explain_result", "propose_recovery", "finish"])
def test_action_schema_rejects_execution_and_removed_action_kinds(kind: str) -> None:
    with pytest.raises(ValueError):
        parse_action_envelope({"kind": kind, "reason": "unsafe", "expected_state": "CREATED"})


def test_action_schema_contains_no_payload_command_or_path_authority() -> None:
    schema = str(action_envelope_json_schema())
    assert "payload" not in schema
    assert "execution_ticket" not in schema
    assert "output_dir" not in schema
