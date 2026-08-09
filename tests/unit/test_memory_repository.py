from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.backend.app.services.memory_repository import (
    MEMORY_SCHEMA_VERSION,
    MemoryRepository,
    MemoryRepositoryError,
)


def _remember(repository: MemoryRepository, **overrides):
    payload = {
        "project_id": "project-a",
        "command_id": "remember-command-0001",
        "principal": "desktop-local-user",
        "kind": "presentation_preference",
        "key": "response-language",
        "value": {"language": "zh-CN"},
        "summary": "Use Chinese for project responses.",
        "impact_class": "presentation",
        "consent_epoch": 1,
    }
    payload.update(overrides)
    return repository.remember_explicit(**payload)


def test_repository_restart_round_trip_and_project_isolation(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    first = MemoryRepository(path)
    result = _remember(first)
    assert result["status"] == "active"

    reopened = MemoryRepository(path)
    items = reopened.list_items(project_id="project-a")
    assert len(items) == 1
    assert items[0].revision.content == {"language": "zh-CN"}
    assert reopened.list_items(project_id="project-b") == []
    assert reopened.health_check() == {
        "ok": True,
        "schema_version": MEMORY_SCHEMA_VERSION,
        "integrity": "ok",
        "path": str(path.resolve()),
        "last_forget_wal_truncate_at": None,
    }


def test_concurrent_repository_initialization_is_serialized(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"

    with ThreadPoolExecutor(max_workers=8) as pool:
        repositories = list(pool.map(lambda _index: MemoryRepository(path), range(8)))

    assert all(repository.health_check()["ok"] is True for repository in repositories)


def test_command_replay_is_idempotent_and_payload_change_conflicts(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    first = _remember(repository)
    assert _remember(repository) == first

    with pytest.raises(MemoryRepositoryError) as error:
        _remember(repository, value={"language": "en"})
    assert error.value.code == "MEMORY_COMMAND_CONFLICT"


def test_high_impact_explicit_memory_remains_candidate(tmp_path: Path) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")
    result = _remember(
        repository,
        command_id="remember-command-0002",
        kind="project_decision",
        key="atlas",
        value={"atlas_id": "atlas-a"},
        summary="Prefer atlas A.",
        impact_class="scientific",
    )
    assert result["status"] == "proposed"
    candidates = repository.list_candidates(project_id="project-a")
    assert len(candidates) == 1
    assert candidates[0].requires_review is True


def test_unknown_schema_version_enters_explicit_failure(tmp_path: Path) -> None:
    import sqlite3

    path = tmp_path / "memory.sqlite"
    MemoryRepository(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE store_meta SET value='future-v9' WHERE key='schema_version'"
        )
    with pytest.raises(MemoryRepositoryError) as error:
        MemoryRepository(path)
    assert error.value.code == "MEMORY_SCHEMA_UNSUPPORTED"


def test_concurrent_writers_cannot_create_two_active_items_for_one_key(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository(tmp_path / "memory.sqlite")

    def write(command_id: str, language: str):
        try:
            return _remember(
                repository,
                command_id=command_id,
                value={"language": language},
            )
        except MemoryRepositoryError as exc:
            return {"error": exc.code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: write(*args),
                [
                    ("remember-command-concurrent-1", "zh-CN"),
                    ("remember-command-concurrent-2", "en"),
                ],
            )
        )
    assert sum(result.get("status") == "active" for result in results) == 1
    assert sum(result.get("error") == "MEMORY_ACTIVE_KEY_CONFLICT" for result in results) == 1
    assert len(repository.list_items(project_id="project-a")) == 1
