"""The deterministic runtime has one process-provider seam."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.main import create_app
from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def test_application_process_calls_are_confined_to_windows_provider() -> None:
    runtime = Path("src/backend/app")
    offenders = []
    for path in runtime.rglob("*.py"):
        if path.name == "windows_process_sandbox.py":
            continue
        text = path.read_text(encoding="utf-8")
        if any(
            token in text
            for token in (
                "subprocess.run(",
                "subprocess.Popen(",
                "subprocess.call(",
                "subprocess.check_",
                "os.system(",
            )
        ):
            offenders.append(path.as_posix())
    assert offenders == []


def test_legacy_sandbox_routes_are_not_exposed() -> None:
    paths = set(create_app().openapi()["paths"])
    assert not any("execute-sandbox" in path or "register-sandbox-" in path for path in paths)
    assert "/api/projects/{project_id}/runs/{run_id}/sandbox-attempts" in paths


def test_unreviewed_process_start_is_rejected() -> None:
    with pytest.raises(SafetyError) as exc_info:
        reject_unreviewed_process_start(["unreviewed-tool", "--unsafe"])

    assert exc_info.value.code == "EXECUTION_CONTRACT_REQUIRED"


def test_external_scientific_process_contracts_remain_unavailable() -> None:
    contracts = [
        contract
        for contract in NODE_CONTRACTS.values()
        if contract.backend in {"matlab-spm", "dpabi"}
    ]

    assert contracts
    assert all(contract.resources.process_mode == "sandbox_process" for contract in contracts)
    assert all(
        contract.capability_level in {"unavailable", "scaffolded"}
        for contract in contracts
    )
    assert all(contract.executable is False for contract in contracts)
