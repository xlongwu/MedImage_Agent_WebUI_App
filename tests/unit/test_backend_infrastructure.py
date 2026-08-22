from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import run_routes
from src.backend.app.core.config import ConfigService, get_backend_settings
from src.backend.app.core.exceptions import PipelineError
from src.backend.app.main import create_app
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.runtime.state_store import (
    STATE_SCHEMA_VERSION,
    write_node_state,
    write_pipeline_summary,
)


def _extract_route_paths(app) -> set[str]:
    """Extract all registered route paths from the OpenAPI schema.

    Uses app.openapi() instead of iterating app.routes directly because
    Starlette 0.40+ wraps routes in Mount/_IncludedRouter objects that are
    not flat-enumerable at the top level.
    """
    schema = app.openapi()
    return set(schema.get("paths", {}).keys())


def _extract_duplicate_routes(app) -> list[str]:
    """Find duplicate (method, path) entries via OpenAPI schema."""
    duplicates: list[str] = []
    schema = app.openapi()
    for path, methods in schema.get("paths", {}).items():
        seen: set[str] = set()
        for method in methods:
            if method in seen:
                duplicates.append(f"{method.upper()} {path}")
            seen.add(method)
    return duplicates


def test_request_id_and_response_time_headers_are_added():
    app = create_app()
    client = TestClient(app)

    response = client.get("/health", headers={"X-Request-ID": "req-test-123"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test-123"
    assert "X-Response-Time-ms" in response.headers


def test_removed_api_v1_compatibility_prefix_fails_closed():
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/health")

    assert response.status_code == 404


def test_domain_routes_register_the_single_agent_task_planning_chain():
    app = create_app()
    registered_paths = _extract_route_paths(app)

    expected_paths = {
        "/health",
        "/api/project-config",
        "/api/dpabi/capability",
        "/api/dpabi/function-list",
        "/api/rsfmri/preprocessing-plan",
        "/api/projects/{project_id}/agent/tasks",
        "/api/projects/{project_id}/agent/tasks/{task_id}",
        "/api/runs",
        "/api/runs/{run_id}",
        "/api/runs/{run_id}/state-detail",
        "/api/runs/{run_id}/diagnosis",
        "/api/retry/dry-run",
        "/api/retry/execute",
        "/api/retry-runs/{retry_run_id}",
        "/api/scheduler/plan",
        "/api/gpu/detect",
        "/api/gpu/synthetic-benchmark",
        "/api/pipelines",
        "/api/files/read",
        "/api/logs/read",
        "/api/sessions/index",
        "/api/history/runs",
        "/api/advisor/protocol",
        "/api/kb/errors",
        "/api/experiments/run-index",
        "/api/artifacts/preview",
        "/api/bundle/create",
        "/api/docs/inventory",
        "/api/real-data/inspect",
        "/api/sandbox/status",
        "/api/workflow/run",
        "/api/deployment/profile",
    }

    assert expected_paths <= registered_paths
    assert "/api/agent/plan" not in registered_paths
    assert "/api/agent-runs/{agent_run_id}" not in registered_paths


def test_runs_routes_preserve_the_established_inspection_contract(monkeypatch):
    monkeypatch.setattr(
        run_routes,
        "list_available_runs",
        lambda work_dir: {"ok": True, "work_dir": work_dir, "runs": []},
    )
    monkeypatch.setattr(
        run_routes,
        "inspect_run",
        lambda run_id, work_dir: {"ok": True, "run_id": run_id, "work_dir": work_dir},
    )
    monkeypatch.setattr(
        run_routes,
        "read_state_detail",
        lambda **kwargs: {"ok": True, **kwargs},
    )
    monkeypatch.setattr(
        run_routes,
        "diagnose_run",
        lambda run_id: {"ok": True, "run_id": run_id},
    )

    client = TestClient(create_app())

    assert client.get("/api/runs").json() == {"ok": True, "work_dir": "./work", "runs": []}
    assert client.get("/api/runs/run-1").json() == {
        "ok": True,
        "run_id": "run-1",
        "work_dir": "./work",
    }
    assert client.get("/api/runs/run-1/state-detail?path=states/run-1.json").json() == {
        "ok": True,
        "run_id": "run-1",
        "state_path": "states/run-1.json",
        "work_dir": "./work",
    }
    assert client.get("/api/runs/run-1/diagnosis").json() == {"ok": True, "run_id": "run-1"}


def test_legacy_file_planning_implementation_and_cli_are_removed():
    removed_paths = (
        "src/backend/app/runtime/agent_plan.py",
        "src/backend/app/runtime/agent_runtime.py",
        "src/backend/app/runtime/tool_registry.py",
        "src/backend/app/runtime/background_review.py",
        "src/backend/app/tools/agent_plan_cli.py",
        "src/backend/app/tools/agent_execute_cli.py",
        "src/backend/app/tools/agent_review_cli.py",
        "src/backend/app/tools/background_review_status.py",
    )

    assert all(not Path(path).exists() for path in removed_paths)
    assert "agent_plan" not in Path("src/backend/app/tools/scheduler_plan_cli.py").read_text(
        encoding="utf-8"
    )


def test_removed_file_planning_routes_fail_closed():
    client = TestClient(create_app())

    assert client.post("/api/agent/plan", json={}).status_code == 404
    assert client.get("/api/agent-runs/legacy").status_code == 404


def test_domain_split_routes_do_not_register_duplicate_method_paths():
    app = create_app()
    duplicates = _extract_duplicate_routes(app)
    assert duplicates == [], f"Duplicate routes: {duplicates}"


def test_openapi_operation_ids_are_unique_and_no_routes_are_deprecated():
    schema = create_app().openapi()
    operations = [
        operation
        for methods in schema.get("paths", {}).values()
        for method, operation in methods.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    ]
    operation_ids = [operation["operationId"] for operation in operations]

    assert len(operation_ids) == len(set(operation_ids))
    assert all(not operation.get("deprecated", False) for operation in operations)


def test_rate_limiter_returns_structured_429(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_RATE_LIMIT_PER_MINUTE", "1")
    app = create_app()
    client = TestClient(app)

    first = client.get("/health", headers={"X-Request-ID": "rate-1"})
    second = client.get("/health", headers={"X-Request-ID": "rate-2"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.headers["X-Request-ID"] == "rate-2"
    assert second.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_medimage_error_handler_returns_stable_error_payload():
    app = create_app()

    @app.get("/test-only/pipeline-error")
    def _raise_pipeline_error():
        raise PipelineError("Bad pipeline", details={"pipeline_id": "p1"})

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/test-only/pipeline-error",
        headers={"X-Request-ID": "req-error-123"},
    )

    assert response.status_code == 400
    assert response.headers["X-Request-ID"] == "req-error-123"
    payload = response.json()
    assert payload == {
        "ok": False,
        "error": {
            "code": "PIPELINE_ERROR",
            "message": "Bad pipeline",
            "details": {"pipeline_id": "p1"},
        },
        "request_id": "req-error-123",
    }


def test_legacy_planner_routes_are_not_registered():
    registered_paths = _extract_route_paths(create_app())
    for route in ("/api/planner/draft", "/api/planner/validate", "/api/planner/execute", "/api/planner/history", "/api/planner/plan-from-goal"):
        assert route not in registered_paths


def test_state_store_writes_versioned_json_atomically(tmp_path):
    node_path = write_node_state(
        run_id="run-1",
        node_id="node-a",
        subject="project",
        status="SUCCESS",
        started_at="2026-06-12T00:00:00+00:00",
        ended_at="2026-06-12T00:00:01+00:00",
        result={"ok": True, "outputs": ["out.txt"]},
        work_dir=str(tmp_path),
    )
    summary_path = write_pipeline_summary(
        run_id="run-1",
        pipeline_id="pipe-a",
        status="SUCCESS",
        started_at="2026-06-12T00:00:00+00:00",
        ended_at="2026-06-12T00:00:01+00:00",
        node_states=[str(node_path)],
        node_results=[{"ok": True}],
        errors=[],
        work_dir=str(tmp_path),
    )

    node_data = json.loads(node_path.read_text(encoding="utf-8"))
    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert node_data["_schema_version"] == STATE_SCHEMA_VERSION
    assert summary_data["_schema_version"] == STATE_SCHEMA_VERSION
    assert node_data["node"] == "node-a"
    assert summary_data["nodes_success"] == 1


def test_atomic_write_json_preserves_existing_file_on_failure(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"ok": true}', encoding="utf-8")

    class NotSerializable:
        pass

    try:
        atomic_write_json(target, {"bad": NotSerializable()})
    except TypeError:
        pass

    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert not list(tmp_path.glob("*.tmp"))


def test_config_service_loads_server_env_with_legacy_settings_shape(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_BACKEND_HOST", "0.0.0.0")
    monkeypatch.setenv("MEDIMAGE_BACKEND_PORT", "8100")
    monkeypatch.setenv("MEDIMAGE_SERVICE_NAME", "medimage-test")

    settings = get_backend_settings()
    service = ConfigService()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8100
    assert settings.service_name == "medimage-test"
    assert service.snapshot().server.port == 8100


def test_config_service_invalid_port_falls_back(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_BACKEND_PORT", "not-a-port")

    assert get_backend_settings().port == 8000


def test_config_service_loads_project_yaml(tmp_path):
    config_path = tmp_path / "project_config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "runtime:",
                "  work_dir: ./work",
                "  log_dir: ./logs",
                "third_party:",
                "  spm_dir: ./third_party/spm12",
                "  dpabi_dir: ./third_party/DPABI",
                "safety:",
                "  rawdata_readonly: true",
            ]
        ),
        encoding="utf-8",
    )

    service = ConfigService.from_yaml(config_path)
    snapshot = service.snapshot()

    assert service.project is not None
    assert service.project.runtime.work_dir == "./work"
    assert snapshot.project is not None
    assert snapshot.project["third_party"]["spm_dir"] == "./third_party/spm12"
