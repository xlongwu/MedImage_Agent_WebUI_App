from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import create_app


def test_preview_graph_endpoint_is_project_scoped_and_read_only(tmp_path):
    project = SimpleNamespace(id="project-1", metadata={"project_dir": str(tmp_path)})

    class Store:
        def get_project(self, project_id):
            return project if project_id == project.id else None

    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: Store()
    client = TestClient(app)
    response = client.post("/api/projects/project-1/plan-graph-preview", json={"plan": {"pipeline_id": "g", "nodes": [{"id": "contract_smoke", "depends_on": []}]}})

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == "project-1"
    assert payload["run_id"] is None
    assert payload["nodes"][0]["parameter_keys"] == []
