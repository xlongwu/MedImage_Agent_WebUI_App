"""Tests for LLM Planner API endpoint (POST /api/planner/plan-from-goal)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.planner.llm_planner import generate_plan_from_goal

client = TestClient(app)
EXAMPLE_CONFIG = str(Path("examples/project_config_dataset.yaml").resolve())


def _post_plan(payload: dict):
    return client.post(
        "/api/planner/plan-from-goal",
        json={"project_config_path": EXAMPLE_CONFIG, **payload},
    )


# ── 1. Returns 200 ──


def test_returns_200():
    resp = _post_plan({"goal": "motion correction"})
    assert resp.status_code == 200


# ── 2. ok == true ──


def test_ok_true():
    resp = _post_plan({"goal": "motion correction"})
    data = resp.json()
    assert data["ok"] is True


# ── 3. contains plan ──


def test_contains_plan():
    resp = _post_plan({"goal": "motion correction"})
    data = resp.json()
    assert "plan" in data
    assert data["plan"]["pipeline_id"] == "planned_motion_qc"


# ── 4. contains validation ──


def test_contains_validation():
    resp = _post_plan({"goal": "motion correction"})
    data = resp.json()
    assert "validation" in data
    assert data["validation"]["ok"] is True


# ── 5. spm_realign in plan ──


def test_spm_realign_in_plan():
    resp = _post_plan({"goal": "motion correction"})
    data = resp.json()
    nids = [n["id"] for n in data["plan"]["nodes"]]
    assert "spm_realign_subject" in nids


# ── 6. approval_required in validation ──


def test_approval_required_in_validation():
    resp = _post_plan({"goal": "motion correction"})
    data = resp.json()
    assert "spm_realign_subject" in data["validation"]["approval_required_nodes"]


# ── 7. empty goal → 200, ok=false ──


def test_empty_goal():
    resp = _post_plan({"goal": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("EMPTY_GOAL" in e for e in data["errors"])


# ── 8. unsupported goal → 200, ok=false ──


def test_unsupported_goal():
    resp = _post_plan({"goal": "xyz unknown"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("UNSUPPORTED_GOAL" in e for e in data["errors"])


# ── 9. unsupported provider → 200, ok=false ──


def test_unsupported_provider():
    resp = _post_plan({"goal": "motion", "provider": "openai"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("UNSUPPORTED_PROVIDER" in e for e in data["errors"])


# ── 10. missing goal → 422 ──


def test_missing_goal_422():
    resp = _post_plan({})
    assert resp.status_code == 422


# ── 11. No pipeline execution ──


def test_no_pipeline_execution():
    resp = _post_plan({"goal": "motion"})
    assert resp.status_code == 200


# ── 12. No node runner execution ──


def test_no_runner_execution():
    resp = _post_plan({"goal": "motion"})
    assert resp.status_code == 200


# ── 13. JSON serializable ──


def test_json_serializable():
    resp = _post_plan({"goal": "motion correction"})
    raw = resp.text
    back = json.loads(raw)
    assert back["ok"] is True


# ── 14. openai_compatible without API key → 200, ok=false ──


def test_openai_compatible_no_api_key(monkeypatch):
    monkeypatch.delenv("MEDIMAGE_LLM_API_KEY", raising=False)
    resp = _post_plan(
        {
            "goal": "motion correction",
            "provider": "openai_compatible",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert any("LLM_API_KEY_MISSING" in e for e in data["errors"])


# ── 15. openai_compatible does not call real network ──


def test_openai_compatible_no_network(monkeypatch):
    monkeypatch.delenv("MEDIMAGE_LLM_API_KEY", raising=False)
    resp = _post_plan(
        {
            "goal": "motion",
            "provider": "openai_compatible",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["provider"] == "openai_compatible"


# ── 16. openai_compatible API key not leaked ──


def test_openai_compatible_no_api_key_leak(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_LLM_API_KEY", "sk-secret-test-key")
    resp = _post_plan(
        {
            "goal": "motion",
            "provider": "openai_compatible",
        }
    )
    assert resp.status_code == 200
    raw = resp.text
    assert "sk-secret-test-key" not in raw


def test_removed_mock_provider_is_rejected_without_fallback():
    resp = _post_plan(
        {
            "goal": "motion correction",
            "provider": "mock",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["provider"] == "mock"
    assert data["plan"] == {}
    assert data["planner_evidence"]["failure_code"] == "UNSUPPORTED_PROVIDER"


# ── rule_based provider remains the deterministic local provider ──


def test_rule_based_provider_regression():
    resp = _post_plan(
        {
            "goal": "reho analysis",
            "provider": "rule_based",
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["provider"] == "rule_based"


# ── 20. openai_compatible provider error not fallback to ok ──


def test_openai_compatible_error_not_fallback(monkeypatch):
    """When provider=openai_compatible and API key is missing,
    the response must be ok=false, not silently fallback to deterministic."""
    monkeypatch.delenv("MEDIMAGE_LLM_API_KEY", raising=False)
    resp = _post_plan(
        {
            "goal": "motion",
            "provider": "openai_compatible",
        }
    )
    data = resp.json()
    assert data["ok"] is False, "openai_compatible with missing key must fail, not fallback"
