from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed
from src.backend.app.schemas.agent_trace import AgentTraceBundle, AgentTraceEntry
from src.backend.app.services.agent_replay_service import AgentReplayService
from src.backend.app.services.agent_trace_service import calculate_trace_integrity_hash

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "agent_harness_replay.json"


def test_offline_replay_corpus_has_30_plus_bilingual_safety_cases() -> None:
    corpus = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    assert len(corpus) >= 30
    assert any(item["id"].startswith("en-") for item in corpus)
    assert any(item["id"].startswith("zh-") for item in corpus)
    assert {"repair_once", "budget_stop", "rejected"} <= {item["expected"] for item in corpus}
    assert any(item["kind"] == "execute" for item in corpus)


@pytest.mark.parametrize("case", json.loads(_FIXTURE.read_text(encoding="utf-8")), ids=lambda item: item["id"])
def test_offline_replay_actions_obey_the_fail_closed_catalog(case) -> None:
    if case["expected"] == "accepted":
        capability = assert_capability_allowed(case["kind"], case["state"])
        assert capability.automation_level in {"A0", "A1"}
        assert capability.side_effect_class in {"read_only", "managed_state"}
    elif case["expected"] == "budget_stop":
        capability = assert_capability_allowed(case["kind"], case["state"])
        assert capability.automation_level in {"A0", "A1"}
        assert capability.side_effect_class in {"read_only", "managed_state"}
    elif case["expected"] == "rejected":
        with pytest.raises(ValueError):
            assert_capability_allowed(case["kind"], case["state"])


@pytest.mark.parametrize("case", json.loads(_FIXTURE.read_text(encoding="utf-8")), ids=lambda item: item["id"])
def test_legacy_safety_corpus_is_exercised_through_the_pure_replay_runner(case) -> None:
    action_kind = case["kind"] if case["expected"] != "repair_once" else None
    entry = AgentTraceEntry(
        step_id=f"step-{case['id']}", step_no=1, idempotency_key=f"key-{case['id']}",
        action_kind=action_kind, action_hash="fixture-action", validation_result="error" if case["expected"] == "repair_once" else "accepted",
        state_before=case["state"], state_after=case["state"], started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:00:00Z",
    )
    draft = AgentTraceBundle(
        trace_id=f"trace-{case['id']}", project_id="fixture-project", lifecycle_id=case["id"],
        entries=(entry,), final_state=case["state"], integrity_status="complete", integrity_hash="pending",
    )
    bundle = draft.model_copy(update={"integrity_hash": calculate_trace_integrity_hash(draft)})

    result = AgentReplayService().replay(bundle)

    if case["expected"] == "accepted":
        assert not any(item.code == "TRACE_CAPABILITY_DENIED" for item in result.violations)
    elif case["expected"] == "rejected":
        assert any(item.code == "TRACE_CAPABILITY_DENIED" for item in result.violations)
    else:
        assert result.integrity_valid
