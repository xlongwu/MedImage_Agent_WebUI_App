from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from src.backend.app.main import app

client = TestClient(app)


def _attach_persisted_review_context(monkeypatch, tmp_path, body):
    from uuid import uuid4

    from src.backend.app.api import execute_reviewed_routes
    from src.backend.app.planner import project_context, reviewed_plan_store
    from src.backend.app.planner.goal_contract_builder import (
        build_goal_contract_semantics,
    )
    from src.backend.app.schemas.desktop import ProjectDetail
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    project_id = f"native-reviewed-{uuid4().hex[:12]}"
    project_dir = tmp_path / project_id
    rawdata_dir = project_dir / "rawdata"
    dataset_index_path = project_dir / "dataset_index.json"
    rawdata_dir.mkdir(parents=True)
    dataset_index_path.write_text('{"subjects": []}', encoding="utf-8")
    config_path = Path(body["project_config_path"])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["runtime"] = {
        "work_dir": str(project_dir / "work"),
        "log_dir": str(project_dir / "logs"),
        "derivatives_dir": str(project_dir / "derivatives"),
        "report_dir": str(project_dir / "reports"),
    }
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    store = SQLiteDesktopStore(tmp_path / f"{project_id}.sqlite")
    store.add_project(
        ProjectDetail(
            id=project_id,
            name="Native reviewed execution",
            study_id=project_id,
            modality="rs-fMRI",
            created_date="test",
            subjects_count=0,
            current_pipeline_id="native_full_preprocessing",
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
            metadata={
                "source": "created",
                "project_dir": str(project_dir),
                "rawdata_dir": str(rawdata_dir),
                "dataset_index_path": str(dataset_index_path),
                "project_config_path": body["project_config_path"],
            },
        ),
        health_status="Ready",
        rawdata_dir=str(rawdata_dir),
    )
    for module in (execute_reviewed_routes, project_context, reviewed_plan_store):
        monkeypatch.setattr(module, "mock_store", store)
    context = project_context.load_project_context(project_id, body["project_config_path"])
    plan = project_context.apply_project_context_to_plan(body["plan"], context)
    body["plan"] = plan
    goal_candidate = build_goal_contract_semantics(plan, "native preprocessing test")
    assert goal_candidate.ok and goal_candidate.semantics is not None
    record = reviewed_plan_store.save_reviewed_plan(
        project_id=project_id,
        project_config_path=body["project_config_path"],
        plan=plan,
        validation={"ok": True},
        goal="native preprocessing test",
        provider="test",
        goal_contract_candidate=goal_candidate.semantics,
        reviewed_actor="test-reviewer",
    )
    body["project_id"] = project_id
    body["reviewed_plan_id"] = record.reviewed_plan_id
    body["approval"]["approval_summary_hash"] = record.payload["approval_envelope"][
        "summary_hash"
    ]
    return body


def _write_project_config(path) -> None:
    config = {
        "project": {"name": "native-preproc-test", "description": "test project"},
        "runtime": {
            "work_dir": str(path.parent / "work"),
            "log_dir": str(path.parent / "logs"),
        },
        "third_party": {
            "spm_dir": str(path.parent / "spm"),
            "dpabi_dir": str(path.parent / "dpabi"),
        },
        "safety": {"rawdata_readonly": True},
    }
    path.write_text(yaml.safe_dump(config), encoding="utf-8")


def _native_execute_plan() -> dict[str, object]:
    return {
        "pipeline_id": "native_full_preprocessing",
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "depends_on": [],
                "params": {
                    "input_bold": (
                        "examples/synthetic_bids/sub-001/func/sub-001_task-rest_bold.nii.gz"
                    ),
                    "sidecar_json": (
                        "examples/synthetic_bids/sub-001/func/sub-001_task-rest_bold.json"
                    ),
                    "output_dir": "derivatives/native-full",
                    "confirmations": {
                        "confirm_reviewed_native_execution": True,
                        "confirm_rawdata_readonly": True,
                        "confirm_no_external_tools": True,
                        "confirm_research_use_only": True,
                        "confirm_no_clinical_use": True,
                    },
                },
            }
        ],
        "metadata": {
            "capability_level": "computed",
            "native_preprocessing": True,
            "execution_requires_approval_gate": True,
        },
    }


def _native_execute_plan_missing_template_and_atlas(tmp_path) -> dict[str, object]:
    input_dir = tmp_path / "inputs" / "sub-001" / "func"
    input_dir.mkdir(parents=True)
    bold = input_dir / "sub-001_task-rest_bold.nii.gz"
    sidecar = input_dir / "sub-001_task-rest_bold.json"
    t1w = tmp_path / "inputs" / "sub-001" / "anat" / "sub-001_T1w.nii.gz"
    t1w.parent.mkdir(parents=True)
    bold.write_bytes(b"placeholder")
    t1w.write_bytes(b"placeholder")
    sidecar.write_text(
        json.dumps({"RepetitionTime": 2.0, "SliceTiming": [0.0, 1.0]}),
        encoding="utf-8",
    )

    plan = _native_execute_plan()
    params = plan["nodes"][0]["params"]  # type: ignore[index]
    params.update(  # type: ignore[union-attr]
        {
            "input_bold": str(bold),
            "sidecar_json": str(sidecar),
            "t1w": str(t1w),
            "output_dir": str(tmp_path / "native-out"),
        }
    )
    return plan


def test_native_full_preprocessing_requires_persisted_review(monkeypatch, tmp_path) -> None:
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes._check_native_preproc_readiness",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path / "reviewed_pipelines",
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kw: {"status": "SUCCESS", "run_id": "native-run-001"},
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": _native_execute_plan(),
            "approval": {
                "approved": True,
                "approved_by": "reviewer",
                "approved_nodes": ["native_preproc_full_execute"],
                "rejected_nodes": [],
                "native_preprocessing_acknowledgement": True,
                "no_external_tools_confirmed": True,
                "rawdata_read_only_confirmed": True,
                "risk_acknowledgement": True,
                "subject_scope_confirmed": True,
            },
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
            "project_config_path": str(cfg),
        },
    )

    payload = response.json()
    assert payload["status"] == "REVIEWED_PLAN_REQUIRED"
    assert payload["execution"]["executor_called"] is False


def test_native_full_preprocessing_unpersisted_failure_request_is_not_dispatched(
    monkeypatch,
    tmp_path,
) -> None:
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes._check_native_preproc_readiness",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path / "reviewed_pipelines",
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kw: {
            "status": "FAILED",
            "run_id": "native-run-002",
            "errors": ["native preprocessing returned partial/blocked stages"],
        },
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": _native_execute_plan(),
            "approval": {
                "approved": True,
                "approved_by": "reviewer",
                "approved_nodes": ["native_preproc_full_execute"],
                "rejected_nodes": [],
                "native_preprocessing_acknowledgement": True,
                "no_external_tools_confirmed": True,
                "rawdata_read_only_confirmed": True,
                "risk_acknowledgement": True,
                "subject_scope_confirmed": True,
            },
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
            "project_config_path": str(cfg),
        },
    )

    payload = response.json()
    assert payload["status"] == "REVIEWED_PLAN_REQUIRED"
    assert payload["ok"] is False
    assert payload["execution"]["executor_called"] is False


def test_native_full_preprocessing_dry_run_blocks_missing_template_and_atlas(
    tmp_path,
) -> None:
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": _native_execute_plan_missing_template_and_atlas(tmp_path),
            "approval": {
                "approved": True,
                "approved_by": "reviewer",
                "approved_nodes": ["native_preproc_full_execute"],
                "rejected_nodes": [],
                "native_preprocessing_acknowledgement": True,
                "no_external_tools_confirmed": True,
                "rawdata_read_only_confirmed": True,
                "risk_acknowledgement": True,
                "subject_scope_confirmed": True,
            },
            "dry_run": True,
            "project_config_path": str(cfg),
        },
    )

    payload = response.json()
    assert payload["status"] == "NATIVE_PREPROC_READINESS_BLOCKED"
    assert payload["ok"] is False
    assert payload["execution"]["executor_called"] is False
    errors = "\n".join(payload["errors"]).lower()
    assert "template" in errors
    assert "atlas" in errors


def test_native_full_preprocessing_execute_blocks_before_executor_when_readiness_fails(
    monkeypatch,
    tmp_path,
) -> None:
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kw: (_ for _ in ()).throw(AssertionError("executor should not run")),
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": _native_execute_plan_missing_template_and_atlas(tmp_path),
            "approval": {
                "approved": True,
                "approved_by": "reviewer",
                "approved_nodes": ["native_preproc_full_execute"],
                "rejected_nodes": [],
                "native_preprocessing_acknowledgement": True,
                "no_external_tools_confirmed": True,
                "rawdata_read_only_confirmed": True,
                "risk_acknowledgement": True,
                "subject_scope_confirmed": True,
            },
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
            "project_config_path": str(cfg),
        },
    )

    payload = response.json()
    assert payload["status"] == "REVIEWED_PLAN_REQUIRED"
    assert payload["ok"] is False
    assert payload["execution"]["executor_called"] is False
    assert payload["pipeline_yaml"]["written"] is False


def test_native_full_preprocessing_persisted_plan_dispatches_with_contract_ticket(
    monkeypatch,
    tmp_path,
) -> None:
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes._check_native_preproc_readiness",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path / "reviewed_pipelines",
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kw: {
            "status": "SUCCESS",
            "run_id": yaml.safe_load(
                Path(kw["pipeline_path"]).read_text(encoding="utf-8")
            )["execution"]["run_id"],
        },
    )
    body = {
        "plan": _native_execute_plan(),
        "approval": {
            "approved": True,
            "approved_by": "reviewer",
            "approved_nodes": ["native_preproc_full_execute"],
            "rejected_nodes": [],
            "native_preprocessing_acknowledgement": True,
            "no_external_tools_confirmed": True,
            "rawdata_read_only_confirmed": True,
            "risk_acknowledgement": True,
            "subject_scope_confirmed": True,
        },
        "dry_run": False,
        "confirm_execution": True,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "project_config_path": str(cfg),
    }
    response = client.post(
        "/api/plans/execute-reviewed",
        json=_attach_persisted_review_context(monkeypatch, tmp_path, body),
    )
    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED"
    assert payload["execution"]["executor_called"] is True
    assert payload["execution_ticket"]["normalized_params_hash"]
    assert payload["execution_ticket"]["contract_versions"] == [
        ["native_preproc_full_execute", "1.1.0"]
    ]


def test_conversion_handoff_readiness_allows_explicit_metric_only_scope(
    tmp_path,
    monkeypatch,
) -> None:
    from src.backend.app.api import execute_reviewed_routes
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSafetyFlags,
        DicomConversionSandboxResult,
    )
    from src.backend.app.services import dicom_conversion_execution

    plan = _native_execute_plan()
    params = plan["nodes"][0]["params"]  # type: ignore[index]
    params.update(  # type: ignore[union-attr]
        {
            "input_bold": "",
            "sidecar_json": "",
            "conversion_run_id": "conv-001",
            "project_id": "project-1",
            "project_dir": str(tmp_path),
            "stage_overrides": {
                "normalization": False,
                "atlas_resampling": False,
                "roi_timeseries": False,
                "functional_connectivity": False,
            },
        }
    )
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    project = SimpleNamespace(metadata={"project_dir": str(tmp_path), "rawdata_dir": str(rawdata)})
    monkeypatch.setattr(
        execute_reviewed_routes,
        "mock_store",
        SimpleNamespace(get_project=lambda project_id: project),
    )
    monkeypatch.setattr(
        dicom_conversion_execution,
        "run_internal_user_dicom_conversion_from_persisted_package",
        lambda *args, **kwargs: DicomConversionSandboxResult(
            ok=True,
            status="ready",
            mode="native",
            project_id="project-1",
            safety_flags=DicomConversionSafetyFlags(),
        ),
    )
    context = SimpleNamespace(
        project_id="project-1",
        project_dir=Path(tmp_path),
        rawdata_dir=rawdata,
        diagnostics={},
    )

    readiness = execute_reviewed_routes._check_native_preproc_readiness(
        plan,
        SimpleNamespace(project_id="project-1"),
        context,
    )

    assert readiness["ok"] is True
    assert readiness["results"][0]["readiness_scope"] == "reviewed_native_conversion_handoff"


def test_conversion_handoff_readiness_blocks_unavailable_template_and_atlas(
    tmp_path,
    monkeypatch,
) -> None:
    from src.backend.app.api import execute_reviewed_routes
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSandboxResult,
    )
    from src.backend.app.services import dicom_conversion_execution

    plan = _native_execute_plan()
    params = plan["nodes"][0]["params"]  # type: ignore[index]
    params.update(  # type: ignore[union-attr]
        {
            "input_bold": "",
            "sidecar_json": "",
            "conversion_run_id": "conv-001",
            "project_id": "project-1",
            "project_dir": str(tmp_path),
        }
    )
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    project = SimpleNamespace(metadata={"project_dir": str(tmp_path), "rawdata_dir": str(rawdata)})
    monkeypatch.setattr(
        execute_reviewed_routes,
        "mock_store",
        SimpleNamespace(get_project=lambda project_id: project),
    )
    monkeypatch.setattr(
        dicom_conversion_execution,
        "run_internal_user_dicom_conversion_from_persisted_package",
        lambda *args, **kwargs: DicomConversionSandboxResult(
            ok=True,
            status="ready",
            mode="native",
            project_id="project-1",
        ),
    )
    context = SimpleNamespace(
        project_id="project-1",
        project_dir=Path(tmp_path),
        rawdata_dir=rawdata,
        diagnostics={},
    )

    readiness = execute_reviewed_routes._check_native_preproc_readiness(
        plan,
        SimpleNamespace(project_id="project-1"),
        context,
    )

    assert readiness["ok"] is False
    assert "template" in " ".join(readiness["errors"])
    assert "atlas" in " ".join(readiness["errors"])
