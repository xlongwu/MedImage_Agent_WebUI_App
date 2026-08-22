from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.goal_contract_helpers import reviewed_goal_candidate

EXTERNAL_BIDS_ENV = "MEDIMAGE_EXTERNAL_BIDS_SMOKE_DIR"
MAX_FILES_ENV = "MEDIMAGE_EXTERNAL_BIDS_SMOKE_MAX_FILES"
HASH_MODE_ENV = "MEDIMAGE_EXTERNAL_BIDS_SMOKE_HASH_MODE"

DEFAULT_MAX_FILES = 5_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_SINGLE_FILE_BYTES = 512 * 1024 * 1024
SMALL_HASH_BYTES = 1 * 1024 * 1024
NIFTI_HASH_BYTES = 64 * 1024 * 1024
MAX_HASHED_SMALL_FILES = 20
MAX_HASHED_NIFTI_FILES = 3

PROHIBITED_NEW_NAMES = {
    "dataset_index.json",
    "data_completeness_report.json",
    "subject_table.csv",
    "pipeline_summary.json",
}
PROHIBITED_NEW_PARTS = {
    "logs",
    "log",
    "reports",
    "report",
    "validation",
    "validations",
    "outputs",
    "derivatives",
    "pipelines",
    "plans",
    "tmp",
    "temp",
}


@dataclass(frozen=True)
class RawdataSnapshot:
    file_paths: tuple[str, ...]
    hashes: dict[str, str]

    @property
    def file_count(self) -> int:
        return len(self.file_paths)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AssertionError(f"{name} must be an integer, got: {value!r}") from exc
    if parsed < 1:
        raise AssertionError(f"{name} must be >= 1, got: {parsed}")
    return parsed


def _dangerous_path_reason(rawdata_dir: Path, original_text: str) -> str | None:
    resolved = rawdata_dir.resolve()
    if resolved == Path(resolved.anchor):
        return "filesystem root directories are not allowed"

    try:
        if resolved == Path.home().resolve():
            return "the current user home root directory is not allowed"
    except OSError:
        pass

    normalized = str(resolved).replace("/", "\\").casefold()
    for system_root in (
        r"c:\windows",
        r"c:\program files",
        r"c:\program files (x86)",
    ):
        if normalized == system_root or normalized.startswith(f"{system_root}\\"):
            return f"Windows system directory is not allowed: {system_root}"

    parts = [part.casefold() for part in resolved.parts]
    if len(parts) >= 2 and parts[1] in {
        "windows",
        "program files",
        "program files (x86)",
    }:
        return "Windows system directory is not allowed"
    if len(parts) <= 3 and any(part in {"users", "home"} for part in parts):
        return "a user home root directory is not allowed"

    posix_text = original_text.replace("\\", "/")
    if posix_text in {"/", "/home", "/Users"}:
        return f"system or home root directory is not allowed: {posix_text}"
    for system_root in ("/System", "/usr", "/bin", "/etc"):
        if posix_text == system_root or posix_text.startswith(f"{system_root}/"):
            return f"system directory is not allowed: {system_root}"

    return None


def _collect_files_bounded(
    rawdata_dir: Path,
    *,
    max_files: int,
) -> tuple[list[Path], int, list[str]]:
    files: list[Path] = []
    total_bytes = 0
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        errors.append(str(exc))

    for current_root, dirnames, filenames in os.walk(rawdata_dir, onerror=onerror):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            path = Path(current_root) / filename
            files.append(path)
            try:
                stat_result = path.stat()
            except OSError as exc:
                errors.append(f"Could not stat {path}: {exc}")
                continue
            total_bytes += stat_result.st_size
            if stat_result.st_size > MAX_SINGLE_FILE_BYTES:
                errors.append(
                    f"File is too large for this smoke test: {path} ({stat_result.st_size} bytes)"
                )
            if len(files) > max_files:
                errors.append(
                    f"rawdata_dir has more than {max_files} files; set "
                    f"{MAX_FILES_ENV} only for a deliberately bounded smoke run"
                )
                return files, total_bytes, errors
            if total_bytes > MAX_TOTAL_BYTES:
                errors.append(
                    f"rawdata_dir exceeds the smoke-test size budget "
                    f"({total_bytes} bytes > {MAX_TOTAL_BYTES} bytes)"
                )
                return files, total_bytes, errors

    return files, total_bytes, errors


def _precheck_external_rawdata(rawdata_dir: Path, original_text: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    max_files = _env_int(MAX_FILES_ENV, DEFAULT_MAX_FILES)

    if not rawdata_dir.exists():
        errors.append(f"Path does not exist: {rawdata_dir}")
        return {"ok": False, "errors": errors, "warnings": warnings}
    if not rawdata_dir.is_dir():
        errors.append(f"Path is not a directory: {rawdata_dir}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    reason = _dangerous_path_reason(rawdata_dir, original_text)
    if reason:
        errors.append(f"Dangerous rawdata_dir rejected: {reason}")

    files, total_bytes, file_errors = _collect_files_bounded(
        rawdata_dir,
        max_files=max_files,
    )
    errors.extend(file_errors)

    has_subject_dirs = any(
        child.is_dir() and child.name.startswith("sub-") for child in rawdata_dir.iterdir()
    )
    has_dataset_description = (rawdata_dir / "dataset_description.json").is_file()
    if not has_subject_dirs:
        warnings.append("No sub-* directories found; API should return diagnostics.")
    if not has_dataset_description:
        warnings.append(
            "dataset_description.json is missing; directory may be BIDS-like but non-standard."
        )
    if os.access(rawdata_dir, os.W_OK):
        warnings.append(
            "rawdata_dir appears writable by the current process; the smoke test "
            "will not write there and will verify immutability afterward."
        )

    return {
        "ok": not errors,
        "path_exists": True,
        "is_directory": True,
        "dangerous_path": reason is not None,
        "has_subject_dirs": has_subject_dirs,
        "has_dataset_description": has_dataset_description,
        "possible_bids_rawdata": has_subject_dirs or has_dataset_description,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "max_files": max_files,
        "rawdata_writable": os.access(rawdata_dir, os.W_OK),
        "external_tools": [],
        "errors": errors,
        "warnings": warnings,
    }


def _relative_file_paths(rawdata_dir: Path, files: list[Path]) -> tuple[str, ...]:
    return tuple(
        sorted(path.relative_to(rawdata_dir).as_posix() for path in files if path.exists())
    )


def _is_nifti_path(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".nii") or name.endswith(".nii.gz")


def _hash_candidates(rawdata_dir: Path, files: list[Path]) -> list[Path]:
    small_files: list[Path] = []
    nifti_files: list[Path] = []

    for path in sorted(files, key=lambda item: item.relative_to(rawdata_dir).as_posix()):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= SMALL_HASH_BYTES and len(small_files) < MAX_HASHED_SMALL_FILES:
            small_files.append(path)
        elif (
            _is_nifti_path(path)
            and size <= NIFTI_HASH_BYTES
            and len(nifti_files) < MAX_HASHED_NIFTI_FILES
        ):
            nifti_files.append(path)

    return small_files + nifti_files


def _rawdata_snapshot(
    rawdata_dir: Path,
    *,
    hash_mode: str,
    max_files: int,
) -> RawdataSnapshot:
    files, _, errors = _collect_files_bounded(rawdata_dir, max_files=max_files)
    if errors:
        raise AssertionError("; ".join(errors))

    paths = _relative_file_paths(rawdata_dir, files)
    if hash_mode == "paths-only":
        return RawdataSnapshot(file_paths=paths, hashes={})
    if hash_mode != "sample-sha256":
        raise AssertionError(
            f"{HASH_MODE_ENV} must be 'paths-only' or 'sample-sha256', got {hash_mode!r}"
        )

    hashes: dict[str, str] = {}
    for path in _hash_candidates(rawdata_dir, files):
        relative = path.relative_to(rawdata_dir).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return RawdataSnapshot(file_paths=paths, hashes=hashes)


def _assert_no_forbidden_rawdata_additions(
    before: RawdataSnapshot,
    after: RawdataSnapshot,
) -> None:
    added = sorted(set(after.file_paths) - set(before.file_paths))
    assert not added, f"rawdata gained files: {added}"

    forbidden = []
    for relative_path in added:
        path = Path(relative_path)
        if path.name in PROHIBITED_NEW_NAMES:
            forbidden.append(relative_path)
            continue
        if any(part.casefold() in PROHIBITED_NEW_PARTS for part in path.parts):
            forbidden.append(relative_path)

    assert not forbidden, f"rawdata gained prohibited outputs: {forbidden}"


def _load_app_modules() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from src.backend.app.api import (
        dashboard_routes,
        execute_reviewed_routes,
        project_routes,
    )
    from src.backend.app.main import app
    from src.backend.app.planner import project_context, reviewed_plan_store
    from src.backend.app.runtime import desktop_config
    from src.backend.app.runtime.pipeline_executor import (
        run_pipeline as real_run_pipeline,
    )
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    return {
        "TestClient": TestClient,
        "app": app,
        "dashboard_routes": dashboard_routes,
        "execute_reviewed_routes": execute_reviewed_routes,
        "project_routes": project_routes,
        "project_context": project_context,
        "reviewed_plan_store": reviewed_plan_store,
        "desktop_config": desktop_config,
        "real_run_pipeline": real_run_pipeline,
        "SQLiteDesktopStore": SQLiteDesktopStore,
    }


def _isolated_store(
    tmp_path: Path,
    monkeypatch,
    modules: dict[str, Any],
):
    desktop_config = modules["desktop_config"]
    project_routes = modules["project_routes"]
    store = modules["SQLiteDesktopStore"](tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(
        desktop_config,
        "DESKTOP_CONFIG_PATH",
        tmp_path / "desktop_config.json",
    )
    monkeypatch.setattr(
        project_routes,
        "DEFAULT_PROJECTS_ROOT",
        tmp_path / "outputs" / "projects",
    )
    for module in (
        project_routes,
        modules["dashboard_routes"],
        modules["project_context"],
        modules["reviewed_plan_store"],
        modules["execute_reviewed_routes"],
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _set_scheduler_gpu_off(project_config_path: str) -> None:
    path = Path(project_config_path)
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scheduler = dict(config.get("scheduler") or {})
    scheduler.update(
        {
            "mode": "sequential",
            "max_workers": 1,
            "matlab_max_workers": 1,
            "gpu_max_workers": 1,
            "gpu_mode": "off",
        }
    )
    config["scheduler"] = scheduler
    path.write_text(
        yaml.safe_dump(
            config,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _safe_reviewed_plan(
    created: dict[str, Any],
    project_context_module,
) -> dict[str, Any]:
    context = project_context_module.load_project_context(
        created["project_id"],
        created["project_config_path"],
    )
    plan = project_context_module.apply_project_context_to_plan(
        {
            "pipeline_id": "external_bids_safe_inspection",
            "nodes": [
                {
                    "id": "data_inspection",
                    "name": "Data Inspection",
                    "backend": "python",
                    "depends_on": [],
                    "inputs": [],
                    "outputs": ["dataset_index"],
                    "params": {
                        "read_nifti_metadata": False,
                        "project_config_path": created["project_config_path"],
                    },
                    "parallel_level": "project",
                    "gpu_supported": False,
                },
            ],
        },
        context,
    )
    plan["nodes"][0]["params"]["dataset_index"] = created["dataset_index_path"]
    return plan


def _execute_body(
    created: dict[str, Any],
    plan: dict[str, Any],
    reviewed_plan_id: str,
) -> dict[str, Any]:
    return {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "external-bids-safe-smoke-test",
            "approved_nodes": ["data_inspection"],
            "rejected_nodes": [],
        },
        "project_id": created["project_id"],
        "reviewed_plan_id": reviewed_plan_id,
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "external-bids-safe-smoke-test",
    }


@pytest.fixture
def external_rawdata(tmp_path: Path, monkeypatch):
    rawdata_text = os.environ.get(EXTERNAL_BIDS_ENV)
    if not rawdata_text:
        pytest.skip(
            f"{EXTERNAL_BIDS_ENV} is not set; provide an absolute BIDS/rawdata "
            "directory to run the external read-only smoke test."
        )

    rawdata_dir = Path(rawdata_text).expanduser().resolve()
    precheck = _precheck_external_rawdata(rawdata_dir, rawdata_text)
    if not precheck.get("ok"):
        pytest.fail("External BIDS smoke precheck failed: " + "; ".join(precheck.get("errors", [])))

    max_files = int(precheck["max_files"])
    hash_mode = os.environ.get(HASH_MODE_ENV, "sample-sha256").strip() or "sample-sha256"
    before = _rawdata_snapshot(
        rawdata_dir,
        hash_mode=hash_mode,
        max_files=max_files,
    )

    modules = _load_app_modules()
    store = _isolated_store(tmp_path, monkeypatch, modules)
    client = modules["TestClient"](modules["app"])

    return {
        "client": client,
        "store": store,
        "modules": modules,
        "rawdata_dir": rawdata_dir,
        "precheck": precheck,
        "before": before,
        "hash_mode": hash_mode,
        "max_files": max_files,
    }


def test_external_bids_safe_reviewed_execute_is_read_only(
    external_rawdata,
    tmp_path: Path,
    monkeypatch,
):
    client = external_rawdata["client"]
    store = external_rawdata["store"]
    modules = external_rawdata["modules"]
    rawdata_dir = external_rawdata["rawdata_dir"]
    precheck = external_rawdata["precheck"]

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "External BIDS Safe Smoke",
            "rawdata_dir": str(rawdata_dir),
            "copy_mode": "reference",
            "run_inspection": True,
            "overwrite": False,
        },
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["ok"] is True
    assert (
        Path(created["project_dir"])
        .resolve()
        .is_relative_to((tmp_path / "outputs" / "projects").resolve())
    )
    assert Path(created["rawdata_dir"]).resolve() == rawdata_dir
    assert Path(created["project_config_path"]).is_file()
    assert created["dataset_index_path"]
    assert Path(created["dataset_index_path"]).is_file()

    _set_scheduler_gpu_off(created["project_config_path"])

    config = yaml.safe_load(Path(created["project_config_path"]).read_text(encoding="utf-8"))
    assert config["data"]["copy_mode"] == "reference"
    assert Path(config["data"]["rawdata_dir"]).resolve() == rawdata_dir
    assert config["data"]["dataset_index"] == created["dataset_index_path"]
    assert config["safety"]["rawdata_readonly"] is True
    assert config["scheduler"]["gpu_mode"] == "off"

    dataset_index = json.loads(Path(created["dataset_index_path"]).read_text(encoding="utf-8"))
    assert dataset_index["dataset_root"] == str(rawdata_dir)
    assert "subjects" in dataset_index
    assert isinstance(created["diagnostics"], dict)
    assert created["diagnostics"].get("status")

    detail = client.get(f"/api/projects/{created['project_id']}")
    assert detail.status_code == 200, detail.text
    metadata = detail.json()["metadata"]
    assert metadata["project_config_path"] == created["project_config_path"]
    assert metadata["dataset_index_path"] == created["dataset_index_path"]
    assert Path(metadata["rawdata_dir"]).resolve() == rawdata_dir

    plan = _safe_reviewed_plan(created, modules["project_context"])
    assert [node["id"] for node in plan["nodes"]] == ["data_inspection"]
    assert plan["nodes"][0]["backend"] == "python"
    assert "create_synthetic_bids" not in json.dumps(plan)
    assert Path(plan["nodes"][0]["params"]["rawdata_dir"]).resolve() == rawdata_dir
    assert plan["nodes"][0]["params"]["dataset_index"] == created["dataset_index_path"]
    assert plan["nodes"][0]["params"]["project_config_path"] == created["project_config_path"]

    goal = "Inspect a user-selected external BIDS/rawdata directory safely"
    saved = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True, "precheck": precheck},
            "goal": goal,
            "provider": "deterministic-test",
            "warnings": list(precheck.get("warnings", [])),
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "external-bids-smoke-test",
        },
    )
    assert saved.status_code == 200, saved.text
    reviewed = saved.json()["reviewed_plan"]
    assert reviewed["reviewed_plan_id"]
    assert Path(reviewed["plan_path"]).is_file()

    listed = client.get(f"/api/projects/{created['project_id']}/plans")
    detail_plan = client.get(
        f"/api/projects/{created['project_id']}/plans/{reviewed['reviewed_plan_id']}"
    )
    assert listed.status_code == detail_plan.status_code == 200
    assert reviewed["reviewed_plan_id"] in {
        item["reviewed_plan_id"] for item in listed.json()["reviewed_plans"]
    }
    assert detail_plan.json()["reviewed_plan"]["payload"]["plan"] == plan

    def fail_if_external_tool_called(*args, **kwargs):
        raise AssertionError("GPU/MATLAB/SPM/DPABI helper must not be called")

    from src.backend.app.runtime import node_registry
    from src.backend.app.tools import gpu_utils

    monkeypatch.setattr(gpu_utils, "detect_gpu", fail_if_external_tool_called)
    monkeypatch.setattr(node_registry, "run_matlab_check", fail_if_external_tool_called)
    monkeypatch.setattr(node_registry, "run_spm_smoke_test", fail_if_external_tool_called)
    monkeypatch.setattr(
        node_registry,
        "run_dpabi_capability_inspection",
        fail_if_external_tool_called,
    )
    execute_reviewed_routes = modules["execute_reviewed_routes"]
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(execute_reviewed_routes, "AUDIT_RECORD_DIR", tmp_path / "audit")
    monkeypatch.setattr(
        execute_reviewed_routes.pipeline_writer,
        "REVIEWED_PIPELINE_DIR",
        tmp_path / "pipelines",
    )
    monkeypatch.setattr(
        execute_reviewed_routes,
        "run_pipeline",
        modules["real_run_pipeline"],
    )

    body = _execute_body(created, plan, reviewed["reviewed_plan_id"])
    first = client.post("/api/plans/execute-reviewed", json=body)
    second = client.post("/api/plans/execute-reviewed", json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()

    for payload in (first_payload, second_payload):
        assert payload["status"] == "EXECUTION_SUBMITTED"
        assert payload["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
        assert payload["run_link_id"]
        assert payload["run_id"]
        assert payload["executor_result"]["status"] == "SUCCESS"
        assert Path(payload["pipeline_path"]).is_file()
        assert Path(payload["summary_path"]).is_file()

        pipeline = yaml.safe_load(Path(payload["pipeline_path"]).read_text(encoding="utf-8"))
        assert [node["id"] for node in pipeline["nodes"]] == ["data_inspection"]
        assert pipeline["nodes"][0]["backend"] == "python"
        assert pipeline["nodes"][0]["gpu_supported"] is False
        assert pipeline["execution"]["run_id"] == payload["run_id"]

        summary = json.loads(Path(payload["summary_path"]).read_text(encoding="utf-8"))
        assert summary["run_id"] == payload["run_id"]
        assert summary["status"] == "SUCCESS"
        assert summary["scheduler"]["gpu_mode"] == "off"

    assert first_payload["run_id"] != second_payload["run_id"]
    assert first_payload["run_link_id"] != second_payload["run_link_id"]

    runs = client.get(f"/api/projects/{created['project_id']}/runs")
    assert runs.status_code == 200
    assert {run["run_id"] for run in runs.json()["runs"]} == {
        first_payload["run_id"],
        second_payload["run_id"],
    }

    for payload in (first_payload, second_payload):
        run_detail = client.get(f"/api/projects/{created['project_id']}/runs/{payload['run_id']}")
        assert run_detail.status_code == 200
        run_link = run_detail.json()["run_link"]
        assert run_link["run_link_id"] == payload["run_link_id"]
        assert run_link["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
        assert run_link["status"] == "SUCCESS"
        assert Path(run_link["pipeline_path"]).is_file()
        assert Path(run_link["summary_path"]).is_file()
        assert store.get_run_link_by_run_id(
            created["project_id"],
            payload["run_id"],
        )

    after = _rawdata_snapshot(
        rawdata_dir,
        hash_mode=external_rawdata["hash_mode"],
        max_files=external_rawdata["max_files"],
    )
    assert after.file_paths == external_rawdata["before"].file_paths
    assert after.file_count == external_rawdata["before"].file_count
    assert after.hashes == external_rawdata["before"].hashes
    _assert_no_forbidden_rawdata_additions(external_rawdata["before"], after)
