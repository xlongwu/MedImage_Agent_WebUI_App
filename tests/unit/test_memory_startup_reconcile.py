from __future__ import annotations

from types import SimpleNamespace

from src.backend.app.core.config_schema import AgentModelRuntimeConfig, MemoryConfig
from src.backend.app import main


def test_memory_startup_reconcile_is_closed_when_install_gate_is_disabled(
    tmp_path, monkeypatch
) -> None:
    config = MemoryConfig(enabled=False, store_path=str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(
        main,
        "ConfigService",
        lambda: SimpleNamespace(memory=config, model=AgentModelRuntimeConfig()),
    )
    monkeypatch.setattr(
        main,
        "MemoryRepository",
        lambda _path: (_ for _ in ()).throw(AssertionError("store must not open")),
    )

    main._run_memory_startup_reconcile()


def test_memory_startup_reconcile_is_bounded_and_runs_ordered_project_work(
    tmp_path, monkeypatch
) -> None:
    config = MemoryConfig(
        enabled=True,
        generation_enabled=True,
        projection_enabled=True,
        store_path=str(tmp_path / "memory.sqlite"),
    )
    projects = [SimpleNamespace(id=f"project-{index}") for index in range(105)]
    calls: list[tuple[str, str]] = []
    repository = SimpleNamespace(health_check=lambda: {"ok": True})

    class Maintenance:
        def __init__(self, **_kwargs):
            pass

        def reconcile_project(self, *, project_id):
            calls.append(("maintenance", project_id))

    class Candidate:
        def __init__(self, **_kwargs):
            pass

        def process_project(self, *, project_id, limit):
            assert limit == 100
            calls.append(("candidate", project_id))

    class Consolidator:
        def __init__(self, **_kwargs):
            pass

        def consolidate_project(self, *, project_id):
            calls.append(("consolidate", project_id))

    class Projector:
        def __init__(self, **_kwargs):
            pass

        def rebuild(self, *, project_id):
            calls.append(("project", project_id))

    monkeypatch.setattr(
        main,
        "ConfigService",
        lambda: SimpleNamespace(memory=config, model=AgentModelRuntimeConfig()),
    )
    monkeypatch.setattr(main, "get_project_store", lambda: SimpleNamespace(list_projects=lambda: projects))
    monkeypatch.setattr(main, "MemoryRepository", lambda _path: repository)
    monkeypatch.setattr(main, "MemoryMaintenanceService", Maintenance)
    monkeypatch.setattr(main, "MemoryCandidateService", Candidate)
    monkeypatch.setattr(main, "MemoryConsolidationService", Consolidator)
    monkeypatch.setattr(main, "MemoryProjectionService", Projector)

    main._run_memory_startup_reconcile()

    assert len(calls) == 400
    assert calls[:4] == [
        ("maintenance", "project-0"),
        ("candidate", "project-0"),
        ("consolidate", "project-0"),
        ("project", "project-0"),
    ]
    assert all(project_id != "project-100" for _, project_id in calls)
