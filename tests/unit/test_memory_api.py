from __future__ import annotations

from fastapi.testclient import TestClient

from src.backend.app.api.dependencies import get_project_store
from src.backend.app.api.memory_dependencies import (
    get_memory_config,
    get_memory_store,
)
from src.backend.app.core.config_schema import MemoryConfig
from src.backend.app.main import create_app
from src.backend.app.services.memory_repository import MemoryRepository
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _client(tmp_path):
    store = SQLiteDesktopStore(tmp_path / "desktop.sqlite")
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    config = MemoryConfig(
        enabled=True,
        generation_enabled=True,
        use_enabled=True,
        store_path=str(tmp_path / "memory.sqlite"),
    )
    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_memory_store] = lambda: repository
    app.dependency_overrides[get_memory_config] = lambda: config
    return TestClient(app), store, repository, config, store.list_projects()[0].id


def test_memory_api_consent_candidate_item_forget_restore_and_preview(tmp_path) -> None:
    client, _store, _repository, _config, project_id = _client(tmp_path)
    consent = client.get(f"/api/projects/{project_id}/memory/consent")
    assert consent.status_code == 200
    assert consent.json()["available"] is True
    assert consent.json()["status"] == "disabled"
    assert consent.json()["retrieval_policy_version"] == "memory-retrieval-v1"
    assert consent.json()["generate_enabled"] is False

    enabled = client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-consent-0001",
            "generate_enabled": True,
            "use_enabled": True,
        },
    )
    assert enabled.status_code == 200
    assert enabled.json()["consent_epoch"] == 1
    assert enabled.json()["status"] == "healthy"

    preference = client.post(
        f"/api/projects/{project_id}/memory/remember",
        json={
            "command_id": "api-remember-pref-0001",
            "kind": "presentation_preference",
            "key": "language",
            "value": {"language": "zh-CN"},
            "summary": "Use Chinese reports.",
            "impact_class": "presentation",
        },
    )
    assert preference.status_code == 200
    memory_id = preference.json()["memory_id"]

    details = client.get(f"/api/projects/{project_id}/memory/items/{memory_id}")
    assert details.status_code == 200
    item = details.json()["item"]
    assert item["revision"]["content"]["language"] == "zh-CN"

    pinned = client.post(
        f"/api/projects/{project_id}/memory/items/{memory_id}/pin",
        json={
            "command_id": "api-pin-0001",
            "expected_item_version": item["item_version"],
            "pinned": True,
        },
    )
    assert pinned.status_code == 200
    pinned_item = client.get(
        f"/api/projects/{project_id}/memory/items/{memory_id}"
    ).json()["item"]

    forgotten = client.post(
        f"/api/projects/{project_id}/memory/items/{memory_id}/forget",
        json={
            "command_id": "api-forget-0001",
            "expected_item_version": pinned_item["item_version"],
            "expected_revision_hash": pinned_item["revision"]["content_hash"],
        },
    )
    assert forgotten.status_code == 200
    forgotten_item = client.get(
        f"/api/projects/{project_id}/memory/items/{memory_id}"
    ).json()["item"]
    assert forgotten_item["status"] == "forgotten"

    restored = client.post(
        f"/api/projects/{project_id}/memory/items/{memory_id}/restore",
        json={
            "command_id": "api-restore-0001",
            "expected_item_version": forgotten_item["item_version"],
            "expected_revision_hash": forgotten_item["revision"]["content_hash"],
            "value": {"language": "zh-CN"},
            "summary": "Use Chinese reports.",
        },
    )
    assert restored.status_code == 200

    scientific = client.post(
        f"/api/projects/{project_id}/memory/remember",
        json={
            "command_id": "api-remember-science-0001",
            "kind": "project_decision",
            "key": "atlas",
            "value": {"decision_kind": "atlas", "value": "schaefer-200"},
            "summary": "Use the confirmed atlas.",
            "impact_class": "scientific",
        },
    )
    candidate_id = scientific.json()["candidate_id"]
    candidate = client.get(
        f"/api/projects/{project_id}/memory/candidates"
    ).json()["items"][0]
    accepted = client.post(
        f"/api/projects/{project_id}/memory/candidates/{candidate_id}/accept",
        json={
            "command_id": "api-candidate-accept-0001",
            "expected_candidate_version": candidate["candidate_version"],
            "candidate_hash": candidate["candidate_hash"],
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["consolidation"]["status"] == "succeeded"

    preview = client.post(
        f"/api/projects/{project_id}/memory/context-preview",
        json={"goal": "Compute atlas connectivity"},
    )
    assert preview.status_code == 200
    assert preview.json()["decision_suggestions"][0]["decision_kind"] == "atlas"
    assert len(client.get(f"/api/projects/{project_id}/memory/events").json()["items"]) > 0


def test_memory_api_denies_invalid_project_actor_spoof_and_stale_mutation(tmp_path) -> None:
    client, _store, _repository, _config, project_id = _client(tmp_path)
    assert client.get("/api/projects/missing/memory/items").status_code == 404
    spoofed = client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-consent-spoof-0001",
            "generate_enabled": True,
            "use_enabled": True,
            "actor": "attacker",
        },
    )
    assert spoofed.status_code == 422

    client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-consent-valid-0001",
            "generate_enabled": True,
            "use_enabled": True,
        },
    )
    created = client.post(
        f"/api/projects/{project_id}/memory/remember",
        json={
            "command_id": "api-remember-stale-0001",
            "kind": "presentation_preference",
            "key": "theme",
            "value": {"theme": "dark"},
            "summary": "Use dark reports.",
            "impact_class": "presentation",
        },
    ).json()
    stale = client.post(
        f"/api/projects/{project_id}/memory/items/{created['memory_id']}/pin",
        json={
            "command_id": "api-pin-stale-0001",
            "expected_item_version": 999,
            "pinned": True,
        },
    )
    assert stale.status_code == 400
    assert stale.json()["error"]["code"] == "MEMORY_ITEM_STALE"


def test_memory_api_reports_unavailable_and_rejects_mutation_when_install_disabled(
    tmp_path,
) -> None:
    client, store, repository, config, project_id = _client(tmp_path)
    disabled = config.model_copy(update={"enabled": False})
    client.app.dependency_overrides[get_memory_config] = lambda: disabled

    status = client.get(f"/api/projects/{project_id}/memory/consent")
    assert status.status_code == 200
    assert status.json()["available"] is False
    assert status.json()["status"] == "disabled"
    assert status.json()["degraded_reason"] == "MEMORY_DISABLED"

    mutation = client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-disabled-consent-0001",
            "generate_enabled": True,
            "use_enabled": True,
        },
    )
    assert mutation.status_code == 400
    assert mutation.json()["error"]["code"] == "MEMORY_DISABLED"
    assert store.get_memory_consent(project_id)["generate_enabled"] is False
    assert repository.list_events(project_id=project_id) == []


def test_memory_api_surfaces_partial_operational_health_without_get_side_effects(
    tmp_path,
) -> None:
    client, store, repository, _config, project_id = _client(tmp_path)
    client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-health-consent-0001",
            "generate_enabled": True,
            "use_enabled": True,
        },
    )
    for _ in range(3):
        repository.record_source_failure(
            project_id=project_id,
            source_sequence=7,
            consent_epoch=1,
            error_code="MEMORY_TEST_FAILURE",
        )
    before = repository.operational_counts(project_id=project_id)

    response = client.get(f"/api/projects/{project_id}/memory/consent")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["dead_letter_jobs"] == 1
    assert payload["store_healthy"] is True
    assert repository.operational_counts(project_id=project_id) == before
    assert store.get_memory_consent(project_id)["consent_epoch"] == 1


def test_enabled_unhealthy_memory_blocks_context_preview_with_structured_error(
    tmp_path, monkeypatch
) -> None:
    client, _store, repository, _config, project_id = _client(tmp_path)
    client.post(
        f"/api/projects/{project_id}/memory/consent",
        json={
            "command_id": "api-unhealthy-consent-0001",
            "generate_enabled": True,
            "use_enabled": True,
        },
    )
    monkeypatch.setattr(
        repository,
        "health_check",
        lambda: {"ok": False, "error_code": "MEMORY_STORE_UNHEALTHY"},
    )

    status = client.get(f"/api/projects/{project_id}/memory/consent")
    preview = client.post(
        f"/api/projects/{project_id}/memory/context-preview",
        json={"goal": "plan"},
    )

    assert status.json()["status"] == "failure"
    assert status.json()["degraded_reason"] == "MEMORY_STORE_UNHEALTHY"
    assert preview.status_code == 400
    assert preview.json()["error"]["code"] == "MEMORY_STORE_UNHEALTHY"
