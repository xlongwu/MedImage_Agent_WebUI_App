from __future__ import annotations

import ast
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.runtime.execution_entry_inventory import (
    EXECUTION_ENTRY_INVENTORY,
)


def test_every_inventory_entry_has_one_allowed_disposition():
    assert EXECUTION_ENTRY_INVENTORY
    assert len({entry.entry_id for entry in EXECUTION_ENTRY_INVENTORY}) == len(
        EXECUTION_ENTRY_INVENTORY
    )
    assert {entry.disposition for entry in EXECUTION_ENTRY_INVENTORY} <= {
        "gateway",
        "proposal/dry-run",
        "deprecated",
    }
    assert sum(entry.disposition == "gateway" for entry in EXECUTION_ENTRY_INVENTORY) == 1


def test_public_api_modules_do_not_import_pipeline_executor_directly():
    api_dir = Path("src/backend/app/api")
    offenders: list[str] = []
    for path in api_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "src.backend.app.runtime.pipeline_executor"
            ):
                offenders.append(str(path))
    assert offenders == []


def test_legacy_agent_execute_is_not_registered():
    assert "/api/agent/execute" not in TestClient(app).app.openapi()["paths"]
