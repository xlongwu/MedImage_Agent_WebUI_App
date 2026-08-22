from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _project(project_id: str, name: str) -> ProjectDetail:
    return ProjectDetail(
        id=project_id,
        name=name,
        study_id=f"study-{project_id}",
        modality="rs-fMRI",
        created_date="2026-08-22",
        subjects_count=1,
        current_pipeline_id="",
        sequences=["BOLD"],
        scans_count=1,
        total_size="0",
        current_model_id="",
        metadata={},
    )


def test_desktop_store_reloads_project_state_after_restart(desktop_store_factory) -> None:
    first = desktop_store_factory("restart")
    first.add_project(
        _project("project-restart", "Before restart"),
        health_status="ready",
        rawdata_dir="",
    )

    restarted = SQLiteDesktopStore(first.db_path)

    assert restarted.get_project("project-restart") == first.get_project("project-restart")
    assert restarted.get_dataset_summary("project-restart").health_status == "ready"


def test_parallel_desktop_stores_are_isolated(desktop_store_factory) -> None:
    stores = [desktop_store_factory("parallel-a"), desktop_store_factory("parallel-b")]

    def write(index: int) -> str:
        store = stores[index]
        name = f"Project {index}"
        store.add_project(
            _project("shared-project-id", name),
            health_status="ready",
            rawdata_dir="",
        )
        return store.get_project("shared-project-id").name

    with ThreadPoolExecutor(max_workers=2) as executor:
        names = list(executor.map(write, range(2)))

    assert names == ["Project 0", "Project 1"]
    assert stores[0].get_project("shared-project-id").name == "Project 0"
    assert stores[1].get_project("shared-project-id").name == "Project 1"


def test_mounted_api_handlers_do_not_access_module_global_store() -> None:
    import ast
    from pathlib import Path

    violations: list[str] = []
    for path in Path("src/backend/app/api").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            mounted = any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr
                in {"get", "post", "put", "patch", "delete", "websocket"}
                for decorator in node.decorator_list
            )
            if not mounted:
                continue
            if any(
                isinstance(candidate, ast.Name)
                and candidate.id == "mock_store"
                for candidate in ast.walk(node)
            ):
                violations.append(f"{path.name}:{node.lineno}:{node.name}")

    assert violations == []
