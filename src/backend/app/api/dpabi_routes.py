"""Domain route handlers extracted from src.backend.app.api.routes.

Endpoint paths and handler bodies are preserved for compatibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api._shared import (
    _load_project_config,
    _read_json_if_exists,
    _read_text_if_exists,
)
from src.backend.app.api.execution_contract import reject_execution_contract
from src.backend.app.api.models import (
    DpabiCapabilityRequest,
    DpabiPreflightRequest,
    DpabiRunPlanRequest,
    DpabiSandboxSmokeRequest,
    DpabiSignatureProbeRequest,
    DpabiSingleFunctionRequest,
    DpabiSubjectSmoothRequest,
    DpabiSubjectWrapperReportRequest,
    DpabiTemplateExecuteRequest,
    DpabiTemplateInstantiateRequest,
    DpabiTemplateWizardRequest,
    DpabiWrapperValidationRequest,
)
from src.backend.app.tools.dpabi_adapter import build_dpabi_input_manifest
from src.backend.app.tools.dpabi_config import write_dpabi_wrapper_scaffold
from src.backend.app.tools.dpabi_contract_registry import write_dpabi_wrapper_contracts
from src.backend.app.tools.dpabi_preflight import run_dpabi_preflight
from src.backend.app.tools.dpabi_run_plan import create_dpabi_run_plan
from src.backend.app.tools.dpabi_subject_wrapper_report import write_dpabi_subject_wrapper_report
from src.backend.app.tools.dpabi_template_instantiator import (
    instantiate_dpabi_template,
    list_dpabi_templates,
)
from src.backend.app.tools.dpabi_template_library import write_dpabi_template_library
from src.backend.app.tools.dpabi_template_wizard import (
    create_dpabi_template_instance_from_wizard,
    get_dpabi_template_wizard_options,
    preview_dpabi_template_instance,
)
from src.backend.app.tools.dpabi_wrapper_validation import write_dpabi_wrapper_validation_matrix

router = APIRouter()


@router.post("/api/dpabi/capability")
def api_dpabi_capability(payload: DpabiCapabilityRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.capability")

@router.post("/api/dpabi/scaffold")
def api_dpabi_scaffold(payload: DpabiCapabilityRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(payload.project_config_path)
        report_dir = project_config.get("runtime", {}).get("report_dir", "./reports")

        result = write_dpabi_wrapper_scaffold(
            capabilities_path=f"{payload.work_dir}/dpabi/dpabi_capabilities.json",
            work_dir=payload.work_dir,
            report_dir=report_dir,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/input-manifest")
def api_dpabi_input_manifest(payload: DpabiPreflightRequest) -> dict[str, Any]:
    try:
        result = build_dpabi_input_manifest(
            dataset_index_path=payload.dataset_index,
            work_dir=payload.work_dir,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/preflight")
def api_dpabi_preflight(payload: DpabiPreflightRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(payload.project_config_path)
        report_dir = project_config.get("runtime", {}).get("report_dir", "./reports")

        result = run_dpabi_preflight(
            work_dir=payload.work_dir,
            report_dir=report_dir,
            capabilities_path=payload.capabilities_path,
            manifest_path=payload.manifest_path,
            wrapper_config_template_path=payload.wrapper_config_template_path,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/run-plan")
def api_dpabi_run_plan(request: DpabiRunPlanRequest) -> dict[str, Any]:
    try:
        result = create_dpabi_run_plan(
            work_dir=request.work_dir,
            report_dir="./reports",
            capabilities_path=request.capabilities_path,
            manifest_path=request.manifest_path,
            preflight_path=request.preflight_path,
            params_path=request.params_path,
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/sandbox-smoke")
def api_dpabi_sandbox_smoke(request: DpabiSandboxSmokeRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.sandbox_smoke")

@router.post("/api/dpabi/subject-smooth")
def api_dpabi_subject_smooth(request: DpabiSubjectSmoothRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.subject_smooth")

@router.post("/api/dpabi/subject-wrapper-report")
def api_dpabi_subject_wrapper_report(request: DpabiSubjectWrapperReportRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(request.project_config_path)
        result = write_dpabi_subject_wrapper_report(
            derivatives_dir=project_config.get("derivatives_dir", "./derivatives"),
            report_dir=project_config.get("runtime", {}).get("report_dir", "./reports"),
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/signature-probe")
def api_dpabi_signature_probe(request: DpabiSignatureProbeRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.signature_probe")

@router.post("/api/dpabi/wrapper-contracts")
def api_dpabi_wrapper_contracts(request: DpabiSignatureProbeRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(request.project_config_path)
        result = write_dpabi_wrapper_contracts(
            signatures_path=f"{request.work_dir}/dpabi/dpabi_function_signatures.json",
            work_dir=request.work_dir,
            report_dir=project_config.get("runtime", {}).get("report_dir", "./reports"),
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/single-function-sandbox")
def api_dpabi_single_function_sandbox(request: DpabiSingleFunctionRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.single_function_sandbox")

@router.post("/api/dpabi/wrapper-validation-matrix")
def api_dpabi_wrapper_validation_matrix(request: DpabiWrapperValidationRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(request.project_config_path)
        result = write_dpabi_wrapper_validation_matrix(
            work_dir=request.work_dir,
            report_dir=project_config.get("runtime", {}).get("report_dir", "./reports"),
            signatures_path=request.signatures_path,
            contracts_path=request.contracts_path,
            sandbox_result_path=request.sandbox_result_path,
            subject_wrapper_summary_path=request.subject_wrapper_summary_path,
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/template-library")
def api_dpabi_template_library(request: DpabiWrapperValidationRequest) -> dict[str, Any]:
    try:
        project_config = _load_project_config(request.project_config_path)
        result = write_dpabi_template_library(
            work_dir=request.work_dir,
            report_dir=project_config.get("runtime", {}).get("report_dir", "./reports"),
            matrix_path=request.signatures_path.replace("dpabi_function_signatures.json", "dpabi_wrapper_compatibility_matrix.json"),
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.get("/api/dpabi/templates")
def api_dpabi_list_templates(work_dir: str = "./work") -> dict[str, Any]:
    try:
        result = list_dpabi_templates(work_dir=work_dir)
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/template-instantiate")
def api_dpabi_template_instantiate(request: DpabiTemplateInstantiateRequest) -> dict[str, Any]:
    try:
        result = instantiate_dpabi_template(
            template_id=request.template_id,
            instance_id=request.instance_id,
            run_id=request.run_id,
            function_name=request.function_name,
            fwhm=request.fwhm,
            subjects=request.subjects,
            work_dir=request.work_dir,
        )
        return result
    except Exception as exc:
        raise_api_error(exc)

@router.post("/api/dpabi/template-execute")
def api_dpabi_template_execute(request: DpabiTemplateExecuteRequest) -> dict[str, Any]:
    reject_execution_contract("dpabi.template.execute")

@router.get("/api/dpabi/template-wizard/options")
def api_dpabi_template_wizard_options() -> dict[str, Any]:
    result = get_dpabi_template_wizard_options("./work")
    if not result.get("ok"):
        return result
    return result

@router.post("/api/dpabi/template-wizard/preview")
def api_dpabi_template_wizard_preview(
    request: DpabiTemplateWizardRequest,
) -> dict[str, Any]:
    result = preview_dpabi_template_instance(
        payload=request.model_dump(),
        work_dir="./work",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result

@router.post("/api/dpabi/template-wizard/create")
def api_dpabi_template_wizard_create(
    request: DpabiTemplateWizardRequest,
) -> dict[str, Any]:
    result = create_dpabi_template_instance_from_wizard(
        payload=request.model_dump(),
        work_dir="./work",
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return result

@router.get("/api/dpabi/template-wizard/latest")
def api_dpabi_template_wizard_latest() -> dict[str, Any]:
    base = Path("outputs/work") / "dpabi" / "template_wizard"

    return {
        "ok": True,
        "latest_preview": _read_json_if_exists(base / "latest_preview.json"),
        "latest_preview_markdown": _read_text_if_exists(base / "latest_preview.md"),
    }

@router.get("/api/dpabi/template-library")
async def dpabi_template_library_view():
    """View the DPABI template library report."""
    path = Path("outputs/reports") / "dpabi" / "dpabi_template_library.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Template library not found. POST /api/dpabi/template-library to generate.")
    return {"ok": True, "report": path.read_text(encoding="utf-8")}


# ── DPABI smoke test / validation endpoints ───────────────────────────────

@router.post("/api/dpabi/smoke-test")
async def dpabi_smoke_test(request: dict[str, Any]):
    """Run DPABI environment smoke test."""
    reject_execution_contract("dpabi.smoke_test")

@router.post("/api/dpabi/run-single-function")
async def dpabi_run_single_function(request: dict[str, Any]):
    """Run a single DPABI function."""
    reject_execution_contract("dpabi.run_single_function")

@router.get("/api/dpabi/function-list")
async def dpabi_function_list():
    """List allowed DPABI functions."""
    from src.backend.app.tools.dpabi_safety import list_allowed_functions

    return {"ok": True, "functions": list_allowed_functions()}


# ── GPU endpoints ─────────────────────────────────────────────────────────
