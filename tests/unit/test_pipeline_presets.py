"""Tests for pipeline preset registry and instantiation."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    preset_routes,
    project_history_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import llm_planner, project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
        project_history_routes,
        execute_reviewed_routes,
        preset_routes,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8"
    )
    return store


def _create_project(client: TestClient, tmp_path: Path) -> dict:
    resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Preset Project",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "preset_proj"),
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Preset list / get ────────────────────────────────────────────────────────


def test_list_presets_returns_rsfmri_preproc_mvp():
    client = TestClient(app)
    resp = client.get("/api/pipeline-presets")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ids = {p["preset_id"] for p in body["presets"]}
    assert "rsfmri_preproc_mvp" in ids


def test_get_preset_returns_nodes():
    client = TestClient(app)
    resp = client.get("/api/pipeline-presets/rsfmri_preproc_mvp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    preset = body["preset"]
    assert preset["preset_id"] == "rsfmri_preproc_mvp"
    assert len(preset["nodes"]) == 6
    assert preset["nodes"][0]["id"] == "data_readiness_check"


def test_get_unknown_preset_returns_404():
    client = TestClient(app)
    resp = client.get("/api/pipeline-presets/nonexistent")
    assert resp.status_code == 404


# ── Instantiate ──────────────────────────────────────────────────────────────


def test_instantiate_project_not_found(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.post(
        "/api/projects/nonexistent/pipeline-presets/rsfmri_preproc_mvp/instantiate", json={}
    )
    assert resp.status_code == 404


def test_instantiate_valid_project_returns_plan(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(
        f"/api/projects/{created['project_id']}/pipeline-presets/rsfmri_preproc_mvp/instantiate",
        json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["preset_id"] == "rsfmri_preproc_mvp"
    plan = body["plan"]
    assert plan["pipeline_id"] == "rsfmri_preproc_mvp"
    assert len(plan["nodes"]) == 6
    assert plan["nodes"][0]["id"] == "data_readiness_check"


def test_instantiated_plan_validates(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    resp = client.post(
        f"/api/projects/{created['project_id']}/pipeline-presets/rsfmri_preproc_mvp/instantiate",
        json={},
    )
    body = resp.json()
    assert body["ok"] is True, f"Validation failed: {body.get('errors', [])}"
    assert body["validation"]["ok"] is True


def test_preset_nodes_in_tool_catalog():
    client = TestClient(app)
    for node_id in [
        "data_readiness_check",
        "bids_validation_check",
        "rsfmri_bold_reference_check",
        "rsfmri_motion_qc_plan",
        "rsfmri_preprocessing_plan_stub",
        "rsfmri_report_plan_stub",
    ]:
        resp = client.get(f"/api/tools/catalog/{node_id}")
        assert resp.status_code == 200, f"{node_id} not in catalog: {resp.text}"
        assert resp.json()["item"]["backend"] == "contract"


# ── Planner integration ─────────────────────────────────────────────────────


def test_planner_maps_rsfmri_preprocessing_goal():
    result = llm_planner.generate_plan_from_goal("rs-fMRI preprocessing", provider="rule_based")
    assert result.ok, f"Planner failed: {result.errors}"
    assert result.plan.get("pipeline_id") == "rsfmri_preproc_mvp"
    assert len(result.plan.get("nodes", [])) == 6


def test_planner_maps_motion_qc_goal():
    result = llm_planner.generate_plan_from_goal("run motion QC", provider="rule_based")
    assert result.ok, f"Planner failed: {result.errors}"
    assert result.plan.get("pipeline_id") == "rsfmri_preproc_mvp"


def test_planner_maps_chinese_goal():
    result = llm_planner.generate_plan_from_goal("静息态预处理", provider="rule_based")
    assert result.ok, f"Planner failed: {result.errors}"
    assert result.plan.get("pipeline_id") == "rsfmri_preproc_mvp"
