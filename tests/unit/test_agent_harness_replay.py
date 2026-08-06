from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed


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
        assert assert_capability_allowed(case["kind"], case["state"]).read_only
    elif case["expected"] == "budget_stop":
        assert assert_capability_allowed(case["kind"], case["state"]).read_only
    elif case["expected"] == "rejected":
        with pytest.raises(ValueError):
            assert_capability_allowed(case["kind"], case["state"])
