"""DPABI single-function wrapper with 4 execution modes."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.backend.app.runtime.external_tool_result import (
    ExternalToolRunResult,
    external_tool_failure,
    from_subprocess_result,
    standard_external_safety,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start
from src.backend.app.tools.dpabi_function_contracts import get_dpabi_single_function_contract
from src.backend.app.tools.dpabi_safety import check_dpabi_call


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _matlab_path(path: str) -> str:
    return path.replace("\\", "/")


def run_dpabi_single_function(
    function_name: str,
    input_bold: str,
    subject_id: str,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    dpabi_dir: str,
    matlab_command: str,
    mode: str = "contract_only",
    approved: bool = False,
    params: dict | None = None,
) -> dict[str, Any]:
    """Run a single DPABI function in the specified mode."""
    # Safety check
    allowed, reason = check_dpabi_call(function_name)
    if not allowed:
        return {"ok": False, "function_name": function_name, "mode": mode, "errors": [reason]}

    # Validate mode
    valid_modes = {"contract_only", "dry_run", "synthetic_execute", "approved_execute"}
    if mode not in valid_modes:
        return {"ok": False, "errors": [f"Invalid mode: {mode}. Use: {valid_modes}"]}

    if mode in ("synthetic_execute", "approved_execute") and not approved:
        return {"ok": False, "errors": [f"approved=true required for {mode} mode"]}

    params = params or {}
    function_contract = get_dpabi_single_function_contract(function_name)
    out_dir = Path(work_dir) / "dpabi" / f"{function_name}_{subject_id}"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_out = Path(log_dir) / f"dpabi_{function_name}_{subject_id}"
    log_out.mkdir(parents=True, exist_ok=True)

    input_manifest = {
        "function_name": function_name,
        "subject_id": subject_id,
        "input_bold": str(Path(input_bold).resolve()),
        "derivatives_dir": str(Path(derivatives_dir).resolve()),
        "params": params,
    }
    (out_dir / "input_manifest.json").write_text(
        json.dumps(input_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Generate MATLAB script
    matlab_script = _generate_dpabi_script(
        function_name=function_name,
        input_bold=input_bold,
        subject_id=subject_id,
        derivatives_dir=derivatives_dir,
        dpabi_dir=dpabi_dir,
        params=params,
        mode=mode,
    )
    script_path = out_dir / "matlab_script.m"
    script_path.write_text(matlab_script, encoding="utf-8")

    output_mapping = _build_output_mapping(function_name, derivatives_dir, subject_id)
    expected_outputs = output_mapping["expected_outputs"]

    if mode in ("contract_only", "dry_run"):
        external_tool_run = ExternalToolRunResult(
            tool_name=f"dpabi.{function_name}",
            backend="matlab-dpabi",
            inputs=[str(Path(input_bold).resolve())] if input_bold else [],
            outputs=[str(out_dir / "output_manifest.json"), str(out_dir / "dpabi_qc.json"), *expected_outputs],
            approval={
                "approved": approved,
                "mode": mode,
                "requires_approval_for_execution": True,
            },
            safety={
                "rawdata_modified": False,
                "files_deleted": False,
                "dparsf_run_called": False,
                "dparsfa_run_called": False,
                "dpabi_gui_called": False,
            },
            warnings=[f"Mode: {mode} -- no DPABI execution performed"] if mode == "contract_only" else [],
        ).finish(returncode=None)
        result = {
            "ok": True,
            "function_name": function_name,
            "subject_id": subject_id,
            "mode": mode,
            "script_path": str(script_path),
            "contract": function_contract,
            "output_mapping": output_mapping,
            "expected_outputs": expected_outputs,
            "outputs": [str(script_path), str(out_dir / "input_manifest.json"),
                         str(out_dir / "output_manifest.json"), str(out_dir / "dpabi_qc.json"), *expected_outputs],
            "warnings": [f"Mode: {mode} -- no DPABI execution performed"] if mode == "contract_only" else [],
            "errors": [],
            "external_tool_run": external_tool_run.to_dict(),
            "external_tool_result": external_tool_run.to_dict(),
        }
        _write_result(out_dir, result)
        return result

    # Execution modes
    stdout_log = log_out / "matlab_stdout.log"
    stderr_log = log_out / "matlab_stderr.log"

    matlab_code = (
        f"addpath('{_matlab_quote(_matlab_path(dpabi_dir))}'); "
        "try; "
        f"run('{_matlab_quote(_matlab_path(str(script_path.resolve())))}'); "
        f"disp('DPABI_OK'); "
        "catch ME; disp(getReport(ME)); exit(1); end; exit(0);"
    )

    is_windows = sys.platform == "win32"
    if is_windows:
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-batch", matlab_code]
    else:
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-r", matlab_code]

    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        completed = reject_unreviewed_process_start(
            cmd, stdout=out, stderr=err, check=False
        )

    result = {
        "ok": completed.returncode == 0,
        "function_name": function_name,
        "subject_id": subject_id,
        "mode": mode,
        "returncode": completed.returncode,
        "script_path": str(script_path),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "contract": function_contract,
        "output_mapping": output_mapping,
        "expected_outputs": expected_outputs,
        "outputs": [str(script_path), str(out_dir / "input_manifest.json"),
                     str(out_dir / "output_manifest.json"), str(out_dir / "dpabi_qc.json"), *expected_outputs],
        "warnings": [],
        "errors": [],
    }

    if completed.returncode != 0:
        result["errors"].append(f"MATLAB exited with return code {completed.returncode}")
    else:
        missing_outputs = [path for path in expected_outputs if not Path(path).exists()]
        if missing_outputs:
            result["ok"] = False
            result["errors"].append(f"Expected DPABI outputs were not found: {missing_outputs}")
        result["qc"] = {
            "ok": not missing_outputs,
            "missing_outputs": missing_outputs,
            "expected_outputs_total": len(expected_outputs),
            "function_name": function_name,
        }

    result["external_tool_run"] = from_subprocess_result(
        tool_name=f"dpabi.{function_name}",
        backend="matlab-dpabi",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(Path(input_bold).resolve())] if input_bold else [],
        outputs=result["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log)},
        approval={
            "approved": approved,
            "mode": mode,
            "requires_human_confirmation": True,
        },
        safety={
            "rawdata_modified": False,
            "files_deleted": False,
            "dparsf_run_called": False,
            "dparsfa_run_called": False,
            "dpabi_gui_called": False,
        },
        errors=result["errors"],
        warnings=result["warnings"],
    )
    result["external_tool_result"] = result["external_tool_run"]

    _write_result(out_dir, result)
    return result


def _expected_output_paths(function_name: str, derivatives_dir: str, subject_id: str) -> list[str]:
    return _build_output_mapping(function_name, derivatives_dir, subject_id)["expected_outputs"]


def _build_output_mapping(function_name: str, derivatives_dir: str, subject_id: str) -> dict[str, Any]:
    base = Path(derivatives_dir) / "dpabi_single_function" / subject_id
    func_dir = base / "func"
    metrics_dir = base / "metrics"
    fc_dir = base / "fc"
    if function_name == "y_Smooth":
        outputs = {"smoothed_nii": str(func_dir / f"{subject_id}_dpabi_smooth.nii")}
    elif function_name == "y_Filter":
        outputs = {"filtered_nii": str(func_dir / f"{subject_id}_dpabi_filtered.nii")}
    elif function_name == "y_RegressOutImgCovariates":
        outputs = {"residual_nii": str(func_dir / f"{subject_id}_dpabi_residual.nii")}
    elif function_name == "y_alff_falff":
        outputs = {
            "alff_nii": str(metrics_dir / f"{subject_id}_dpabi_alff.nii"),
            "falff_nii": str(metrics_dir / f"{subject_id}_dpabi_falff.nii"),
        }
    elif function_name == "y_Reho":
        outputs = {"reho_nii": str(metrics_dir / f"{subject_id}_dpabi_reho.nii")}
    elif function_name == "y_ROItseries":
        outputs = {"roi_timeseries_tsv": str(fc_dir / f"{subject_id}_dpabi_roi_timeseries.tsv")}
    elif function_name == "y_FC":
        outputs = {"fc_matrix_tsv": str(fc_dir / f"{subject_id}_dpabi_fc_matrix.tsv")}
    else:
        outputs = {}
    return {
        "function_name": function_name,
        "subject_id": subject_id,
        "base_dir": str(base),
        "outputs_by_role": outputs,
        "expected_outputs": list(outputs.values()),
    }


def _generate_dpabi_script(
    function_name: str,
    input_bold: str,
    subject_id: str,
    derivatives_dir: str,
    dpabi_dir: str,
    params: dict,
    mode: str,
) -> str:
    """Generate a DPABI MATLAB script."""
    bold_path = _matlab_path(str(Path(input_bold).resolve()))
    deriv_path = _matlab_path(str(Path(derivatives_dir).resolve()))
    dpabi_path = _matlab_path(dpabi_dir)
    output_mapping = _build_output_mapping(function_name, derivatives_dir, subject_id)
    outputs_by_role = output_mapping["outputs_by_role"]

    lines = [
        f"%% DPABI {function_name} - Subject: {subject_id}",
        f"%% Mode: {mode}",
        "%% Generated by MedImage Agent v0.3.0-beta",
        "",
        f"addpath('{dpabi_path}');",
        f"addpath(genpath('{dpabi_path}'));",
        "",
        "%% Input",
        f"input_bold = '{bold_path}';",
        f"output_dir = '{deriv_path}';",
        "",
        "%% Audited output mapping",
    ]
    for role, output_path in outputs_by_role.items():
        parent = _matlab_path(str(Path(output_path).parent.resolve()))
        lines += [
            f"{role}_path = '{_matlab_path(str(Path(output_path).resolve()))}';",
            f"if ~exist('{parent}', 'dir'), mkdir('{parent}'); end",
        ]
    lines.append("")

    if function_name == "y_Smooth":
        fwhm = params.get("fwhm", [6, 6, 6])
        lines += [
            "%% y_Smooth: spatial smoothing",
            f"FWHM = [{fwhm[0]} {fwhm[1]} {fwhm[2]}];",
            "y_Smooth(input_bold, smoothed_nii_path, FWHM);",
            "disp('y_Smooth completed.');",
        ]
    elif function_name == "y_Filter":
        band = params.get("band", [0.01, 0.08])
        tr = params.get("tr", 2.0)
        lines += [
            "%% y_Filter: temporal filtering",
            f"TR = {tr};",
            f"Band = [{band[0]} {band[1]}];",
            "y_Filter(input_bold, filtered_nii_path, TR, Band);",
            "disp('y_Filter completed.');",
        ]
    elif function_name == "y_RegressOutImgCovariates":
        covariate_def = params.get("covariate_def", "Friston24")
        lines += [
            "%% y_RegressOutImgCovariates: nuisance regression",
            f"CovariateDef = '{covariate_def}';",
            "y_RegressOutImgCovariates(input_bold, residual_nii_path, CovariateDef);",
            "disp('y_RegressOutImgCovariates completed.');",
        ]
    elif function_name == "y_alff_falff":
        band = params.get("band", [0.01, 0.08])
        tr = params.get("tr", 2.0)
        lines += [
            "%% y_alff_falff: ALFF/fALFF computation",
            f"TR = {tr};",
            f"Band = [{band[0]} {band[1]}];",
            "y_alff_falff(input_bold, alff_nii_path, falff_nii_path, TR, Band);",
            "disp('y_alff_falff completed.');",
        ]
    elif function_name == "y_Reho":
        neighborhood = params.get("neighborhood", 27)
        lines += [
            "%% y_Reho: Regional Homogeneity",
            f"Neighborhood = {neighborhood};",
            "y_Reho(input_bold, reho_nii_path, Neighborhood);",
            "disp('y_Reho completed.');",
        ]
    elif function_name == "y_ROItseries":
        atlas_file = params.get("atlas_file", "")
        if atlas_file:
            lines += [
                "%% y_ROItseries: ROI time series extraction",
                f"AtlasFile = '{_matlab_path(str(Path(atlas_file).resolve()))}';",
                "ROITimeSeries = y_ROItseries(input_bold, AtlasFile);",
                "writematrix(ROITimeSeries, roi_timeseries_tsv_path, 'FileType', 'text', 'Delimiter', '\\t');",
                "disp('y_ROItseries completed.');",
            ]
        else:
            lines += [
                "%% y_ROItseries: ROI time series extraction",
                "AtlasFile = '';  %% Auto-generate atlas",
                "ROITimeSeries = y_ROItseries(input_bold, AtlasFile);",
                "writematrix(ROITimeSeries, roi_timeseries_tsv_path, 'FileType', 'text', 'Delimiter', '\\t');",
                "disp('y_ROItseries completed.');",
            ]
    elif function_name == "y_FC":
        atlas_file = params.get("atlas_file", "")
        if atlas_file:
            lines += [
                "%% y_FC: Functional Connectivity",
                f"AtlasFile = '{_matlab_path(str(Path(atlas_file).resolve()))}';",
                "FCMatrix = y_FC(input_bold, AtlasFile);",
                "writematrix(FCMatrix, fc_matrix_tsv_path, 'FileType', 'text', 'Delimiter', '\\t');",
                "disp('y_FC completed.');",
            ]
        else:
            lines += [
                "%% y_FC: Functional Connectivity",
                "AtlasFile = '';  %% Auto-generate atlas",
                "FCMatrix = y_FC(input_bold, AtlasFile);",
                "writematrix(FCMatrix, fc_matrix_tsv_path, 'FileType', 'text', 'Delimiter', '\\t');",
                "disp('y_FC completed.');",
            ]
    else:
        lines += [
            f"%% {function_name}: auto-generated stub",
            f"disp('DPABI function {function_name} -- stub mode');",
            "disp('Full implementation requires function-specific parameter mapping.');",
        ]

    return "\n".join(lines) + "\n"


def _write_result(out_dir: Path, result: dict[str, Any]) -> None:
    expected_outputs = result.get("expected_outputs", [])
    observed_outputs = [path for path in expected_outputs if Path(path).exists()]
    missing_outputs = [path for path in expected_outputs if not Path(path).exists()]

    (out_dir / "dpabi_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Write output manifest for reproducibility
    manifest = {
        "run_id": result.get("subject_id", "unknown"),
        "function_name": result.get("function_name", ""),
        "mode": result.get("mode", ""),
        "ok": result.get("ok", False),
        "script_path": result.get("script_path", ""),
        "stdout_log": result.get("stdout_log", ""),
        "stderr_log": result.get("stderr_log", ""),
        "outputs": result.get("outputs", []),
        "expected_outputs": expected_outputs,
        "observed_outputs": observed_outputs,
        "missing_outputs": missing_outputs,
        "output_mapping": result.get("output_mapping", {}),
        "contract": result.get("contract"),
        "errors": result.get("errors", []),
    }
    (out_dir / "output_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # Write minimal QC stub
    qc = {
        "ok": result.get("ok", False),
        "function_name": result.get("function_name", ""),
        "subject_id": result.get("subject_id", ""),
        "mode": result.get("mode", ""),
        "returncode": result.get("returncode", -1),
        "dpabi_qc_status": "PASS" if result.get("ok") else "FAIL",
        "expected_outputs_total": len(expected_outputs),
        "observed_outputs_total": len(observed_outputs),
        "missing_outputs": missing_outputs,
        "contract_qc": (result.get("contract") or {}).get("qc", []),
    }
    (out_dir / "dpabi_qc.json").write_text(
        json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_dpabi_smoke_test(
    dpabi_dir: str,
    matlab_command: str,
    work_dir: str,
    log_dir: str,
    approved: bool = False,
) -> dict[str, Any]:
    """Run DPABI smoke test to verify DPABI environment."""
    if not approved:
        errors = ["approved=true required for DPABI smoke test"]
        return {
            "ok": False,
            "errors": errors,
            "external_tool_result": external_tool_failure(
                tool_name="dpabi.smoke_test",
                backend="matlab-dpabi",
                errors=errors,
                approval={"approved": False, "required": True},
                safety=standard_external_safety(),
            ),
        }

    out_dir = Path(work_dir) / "dpabi_smoke_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_out = Path(log_dir)
    log_out.mkdir(parents=True, exist_ok=True)

    matlab_code = (
        f"addpath('{_matlab_quote(_matlab_path(dpabi_dir))}'); "
        f"addpath(genpath('{_matlab_quote(_matlab_path(dpabi_dir))}')); "
        "try; "
        "disp(['DPABI path: ' which('dpabi')]); "
        "disp(['y_Smooth path: ' which('y_Smooth')]); "
        "disp('DPABI smoke test: OK'); "
        "catch ME; disp(getReport(ME)); exit(1); end; exit(0);"
    )

    is_windows = sys.platform == "win32"
    if is_windows:
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-batch", matlab_code]
    else:
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-r", matlab_code]

    stdout_log = log_out / "dpabi_smoke_stdout.log"
    stderr_log = log_out / "dpabi_smoke_stderr.log"

    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        completed = reject_unreviewed_process_start(
            cmd, stdout=out, stderr=err, check=False
        )

    result = {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "outputs": [str(stdout_log), str(stderr_log), str(out_dir / "dpabi_smoke_result.json")],
        "warnings": [],
        "errors": [],
    }
    if completed.returncode != 0:
        result["ok"] = False
        result["errors"].append(f"MATLAB exited with return code {completed.returncode}")

    result["external_tool_result"] = from_subprocess_result(
        tool_name="dpabi.smoke_test",
        backend="matlab-dpabi",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(Path(dpabi_dir).resolve())],
        outputs=result["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=result["errors"],
        warnings=result["warnings"],
    )
    (out_dir / "dpabi_smoke_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
