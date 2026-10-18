"""Domain route handlers extracted from src.backend.app.api.routes.

Endpoint paths and handler bodies are preserved for compatibility.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.backend.app.api._shared import (
    _load_project_config,
    _read_json_if_exists,
    _read_text_if_exists,
)
from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.api.models import (
    ReleaseReadinessRequest,
    RsfmriAlffFalffRequest,
    RsfmriCoregistrationQcRequest,
    RsfmriFunctionalConnectivityRequest,
    RsfmriGroupSummaryRequest,
    RsfmriNormalizationQcRequest,
    RsfmriNuisanceRegressionRequest,
    RsfmriRehoRequest,
    RsfmriReportExportRequest,
    RsfmriReportValidationRequest,
    RsfmriSegmentationTissueQcRequest,
    RsfmriSmoothingQcRequest,
    RsfmriSpmRealignMotionQcRequest,
    RsfmriSpmSliceTimingRequest,
    RsfmriStRealignMotionQcRequest,
    RsfmriTemporalFilteringRequest,
)
from src.backend.app.schemas.pipeline_schema import load_pipeline_yaml
from src.backend.app.tools.report_exporter import (
    get_latest_rsfmri_report_export,
    list_rsfmri_report_exports,
)
from src.backend.app.tools.report_package_validator import (
    get_latest_rsfmri_report_validation,
    list_rsfmri_report_validations,
)
from src.backend.app.tools.rsfmri_plan_tool import write_rsfmri_preprocessing_plan

router = APIRouter()


def _reject_legacy_pipeline_execution(**_: object) -> dict[str, Any]:
    reject_execution_contract("rsfmri.legacy_pipeline")


def _with_node_state_details(result: dict[str, Any]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    node_errors: list[str] = []
    node_warnings: list[str] = []
    for state_path in result.get("node_states", []) or []:
        if not isinstance(state_path, str):
            continue
        state = _read_json_if_exists(state_path)
        if not state:
            continue
        errors = state.get("errors") if isinstance(state.get("errors"), list) else []
        warnings = state.get("warnings") if isinstance(state.get("warnings"), list) else []
        details.append(
            {
                "node": state.get("node"),
                "status": state.get("status"),
                "outputs": state.get("outputs") if isinstance(state.get("outputs"), list) else [],
                "warnings": warnings,
                "errors": errors,
            }
        )
        node_errors.extend(str(item) for item in errors)
        node_warnings.extend(str(item) for item in warnings)
    if details:
        result = {**result, "node_state_details": details}
    if node_errors:
        result = {**result, "node_errors": node_errors}
    if node_warnings:
        result = {**result, "node_warnings": node_warnings}
    return result


@router.post("/api/rsfmri/spm-chain-validate")
def api_spm_chain_validate() -> dict[str, Any]:
    from src.backend.app.tools.spm_chain_validator import validate_spm_chain_contracts

    result = validate_spm_chain_contracts()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.post("/api/rsfmri/chain-report")
def api_rsfmri_chain_report() -> dict[str, Any]:
    from src.backend.app.tools.rsfmri_chain_report import build_rsfmri_chain_report

    result = build_rsfmri_chain_report("./work", "./reports")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/api/rsfmri/chain-report/latest")
def api_rsfmri_chain_report_latest() -> dict[str, Any]:
    path = Path("outputs/reports") / "rsfmri" / "rsfmri_chain_report.md"
    content = _read_text_if_exists(path)
    if content is None:
        raise HTTPException(
            status_code=404, detail="No chain report found. POST /api/rsfmri/chain-report first."
        )
    return {"ok": True, "report": content}


# ── rs-fMRI preprocessing plan ────────────────────────────────────────────


@router.post("/api/rsfmri/preprocessing-plan")
def api_rsfmri_preprocessing_plan(work_dir: str = "./work") -> dict[str, Any]:

    result = write_rsfmri_preprocessing_plan(work_dir=work_dir, report_dir="./reports")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result


@router.get("/api/rsfmri/preprocessing-plan/latest")
def api_rsfmri_preprocessing_plan_latest() -> dict[str, Any]:
    path = Path("outputs/reports") / "rsfmri" / "rsfmri_preprocessing_plan.md"
    content = _read_text_if_exists(path)
    if content is None:
        raise HTTPException(status_code=404, detail="No plan found.")
    return {"ok": True, "plan": content}


# ── rs-fMRI SPM pipeline endpoints ────────────────────────────────────────


@router.post("/api/rsfmri/spm/realign-motion-qc")
def api_rsfmri_spm_realign_motion_qc(payload: RsfmriSpmRealignMotionQcRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/spm/slice-timing")
def api_rsfmri_spm_slice_timing(payload: RsfmriSpmSliceTimingRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/st/realign-motion-qc")
def api_rsfmri_st_realign_motion_qc(payload: RsfmriStRealignMotionQcRequest) -> dict[str, Any]:
    from pathlib import Path

    import yaml

    _project_config = _load_project_config(payload.project_config_path)
    pipeline = load_pipeline_yaml(payload.pipeline_path)

    # Inject approved=true into the SPM realign node for chain execution
    approved_pipeline_path = None
    if payload.approved:
        for node in pipeline.nodes:
            if node.id == "spm_realign_subject":
                node.params["approved"] = True

        # Write a temporary pipeline YAML with approved set
        approved_pipeline_path = Path(payload.pipeline_path).with_suffix(".approved.yaml")
        pipeline_data = (
            yaml.safe_load(Path(payload.pipeline_path).read_text(encoding="utf-8")) or {}
        )
        for node in pipeline_data.get("nodes", []):
            if node.get("id") == "spm_realign_subject":
                node.setdefault("params", {})
                node["params"]["approved"] = True
        approved_pipeline_path.write_text(
            yaml.safe_dump(pipeline_data, sort_keys=False), encoding="utf-8"
        )

    pipeline_to_run = (
        str(approved_pipeline_path) if approved_pipeline_path else payload.pipeline_path
    )
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=pipeline_to_run,
    )

    if approved_pipeline_path and approved_pipeline_path.exists():
        try:
            approved_pipeline_path.unlink()
        except OSError:
            pass

    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/coregistration-qc")
def api_rsfmri_coregistration_qc(payload: RsfmriCoregistrationQcRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/segmentation-tissue-qc")
def api_rsfmri_segmentation_tissue_qc(payload: RsfmriSegmentationTissueQcRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/normalization-qc")
def api_rsfmri_normalization_qc(payload: RsfmriNormalizationQcRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/smoothing-qc")
def api_rsfmri_smoothing_qc(payload: RsfmriSmoothingQcRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/nuisance-regression")
def api_rsfmri_nuisance_regression(payload: RsfmriNuisanceRegressionRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/temporal-filtering")
def api_rsfmri_temporal_filtering(payload: RsfmriTemporalFilteringRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/alff-falff")
def api_rsfmri_alff_falff(payload: RsfmriAlffFalffRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/reho")
def api_rsfmri_reho(payload: RsfmriRehoRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/functional-connectivity")
def api_rsfmri_functional_connectivity(
    payload: RsfmriFunctionalConnectivityRequest,
) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/group-summary")
def api_rsfmri_group_summary(payload: RsfmriGroupSummaryRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/report-export")
def api_rsfmri_report_export(payload: RsfmriReportExportRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    result = _with_node_state_details(result)
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


@router.post("/api/rsfmri/report-validation")
def api_rsfmri_report_validation(payload: RsfmriReportValidationRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    result = _with_node_state_details(result)
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


# Backward-compatible aliases for desktop clients packaged before route cleanup.
@router.post("/api/rsfmri/report-export/run")
def api_rsfmri_report_export_legacy(payload: RsfmriReportExportRequest) -> dict[str, Any]:
    return api_rsfmri_report_export(payload)


@router.post("/api/rsfmri/report-validator/run")
def api_rsfmri_report_validation_legacy(payload: RsfmriReportValidationRequest) -> dict[str, Any]:
    return api_rsfmri_report_validation(payload)


@router.post("/api/release-readiness")
def api_release_readiness(payload: ReleaseReadinessRequest) -> dict[str, Any]:
    result = _reject_legacy_pipeline_execution(
        project_config_path=payload.project_config_path,
        pipeline_path=payload.pipeline_path,
    )
    if result.get("status") in {"SUCCESS", "PARTIAL"}:
        return {"ok": True, **result}
    raise HTTPException(status_code=400, detail=result)


# ── Report export / validation listing endpoints ──────────────────────────


@router.get("/api/rsfmri/report-exports")
def api_rsfmri_list_report_exports() -> dict[str, Any]:
    export_map = list_rsfmri_report_exports("./exports")
    return {"ok": True, "exports": export_map}


@router.get("/api/rsfmri/report-exports/latest")
def api_rsfmri_get_latest_report_export() -> dict[str, Any]:
    result = get_latest_rsfmri_report_export("./exports")
    if result is None:
        raise HTTPException(status_code=404, detail="No report exports found")
    return {"ok": True, **result}


@router.get("/api/rsfmri/report-validations")
def api_rsfmri_list_report_validations() -> dict[str, Any]:
    validation_map = list_rsfmri_report_validations("./exports")
    return {"ok": True, "validations": validation_map}


@router.get("/api/rsfmri/report-validations/latest")
def api_rsfmri_get_latest_report_validation() -> dict[str, Any]:
    result = get_latest_rsfmri_report_validation("./exports")
    if result is None:
        raise HTTPException(status_code=404, detail="No report validations found")
    return {"ok": True, **result}


@router.get("/api/rsfmri/report-export/list")
def api_rsfmri_list_report_exports_legacy() -> dict[str, Any]:
    return api_rsfmri_list_report_exports()


@router.get("/api/rsfmri/report-export/latest")
def api_rsfmri_get_latest_report_export_legacy() -> dict[str, Any]:
    return api_rsfmri_get_latest_report_export()


@router.get("/api/rsfmri/report-validator/list")
def api_rsfmri_list_report_validations_legacy() -> dict[str, Any]:
    return api_rsfmri_list_report_validations()


@router.get("/api/rsfmri/report-validator/latest")
def api_rsfmri_get_latest_report_validation_legacy() -> dict[str, Any]:
    return api_rsfmri_get_latest_report_validation()


# ── Reproducibility bundle ────────────────────────────────────────────────
