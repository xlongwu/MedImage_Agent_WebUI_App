from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.planner.plan_validator import validate_plan


@pytest.mark.parametrize(
    "node_id",
    ("gui_acpc_manual", "gui_acpc_location", "gui_spm_assist", "gui_open_batch"),
)
def test_removed_gui_node_has_stable_validator_error(node_id: str) -> None:
    result = validate_plan(
        {"pipeline_id": "legacy_gui_plan", "nodes": [{"id": node_id, "depends_on": [], "params": {}}]}
    )

    assert result.ok is False
    assert [issue.code for issue in result.errors] == ["LEGACY_GUI_AGENT_NODE_REMOVED"]
    assert result.unknown_nodes == [node_id]


def test_removed_gui_http_surface_returns_not_found() -> None:
    response = TestClient(app).post("/api/gui-agent/sessions", json={})

    assert response.status_code == 404
