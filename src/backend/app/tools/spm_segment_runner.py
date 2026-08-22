from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.backend.app.runtime.external_tool_result import (
    external_tool_failure,
    from_subprocess_result,
    missing_output_errors,
    standard_external_safety,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start
from src.backend.app.tools.tissue_qc import compute_tissue_qc_for_subject


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _expected_coreg_t1w(subject_id: str, derivatives_dir: str) -> Path:
    return (
        Path(derivatives_dir)
        / "rsfmri_preproc"
        / subject_id
        / "anat"
        / f"coreg_{subject_id}_T1w.nii"
    )


def _is_safe_coreg_t1w(input_t1w: Path, subject_id: str, derivatives_dir: str) -> bool:
    return input_t1w.resolve() == _expected_coreg_t1w(subject_id, derivatives_dir).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_spm_segment_subject(
    matlab_command: str,
    spm_dir: str,
    subject_id: str,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    approved: bool = False,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "spm_segment_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["SPM segmentation requires approved=true."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="spm.segment",
            backend="matlab-spm",
            errors=data["errors"],
            inputs=[],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    # ── M6-T008b: MATLAB/SPM safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_spm_runtime_config

    safety_result = validate_spm_runtime_config(
        matlab_command=matlab_command,
        spm_dir=spm_dir,
    )
    if not safety_result.ok:
        return {
            "ok": False,
            "node_id": "spm_segment_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                f"MATLAB/SPM safety preflight failed: {e.message}"
                for e in safety_result.errors
            ],
            "safety": safety_result.to_dict(),
            "matlab_called": False,
            "spm_called": False,
            "stage": "matlab_safety_preflight",
        }

    # ── M6-T008b: TPM existence check ──
    tpm_path = Path(spm_dir) / "tpm" / "TPM.nii"
    if not tpm_path.exists():
        return {
            "ok": False,
            "node_id": "spm_segment_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"SPM TPM not found at expected path: {tpm_path}"],
            "matlab_called": False,
            "spm_called": False,
            "stage": "tpm_preflight",
        }

    input_t1w = _expected_coreg_t1w(subject_id, derivatives_dir)

    if not input_t1w.exists():
        return {
            "ok": False,
            "node_id": "spm_segment_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Expected coregistered T1w not found: {input_t1w}"],
        }

    if not _is_safe_coreg_t1w(input_t1w, subject_id, derivatives_dir):
        return {
            "ok": False,
            "node_id": "spm_segment_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Unsafe segmentation input: {input_t1w}"],
        }

    anat_dir = input_t1w.parent

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / f"{subject_id}_spm_segment_stdout.log"
    stderr_log = log_path / f"{subject_id}_spm_segment_stderr.log"
    result_json = anat_dir / "spm_segmentation_result.json"

    matlab_script_path = str(Path(matlab_script_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"spm_segment_wrapper('{_matlab_quote(str(Path(spm_dir).resolve()))}', "
        f"'{_matlab_quote(str(input_t1w.resolve()))}', "
        f"'{_matlab_quote(str(result_json.resolve()))}'); "
        "catch ME, disp(getReport(ME)); exit(1); end; exit(0);"
    )

    if sys.platform == "win32":
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-batch", matlab_code]
    else:
        cmd = [matlab_command, "-nodisplay", "-nosplash", "-r", matlab_code]

    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        completed = reject_unreviewed_process_start(
            cmd, stdout=out, stderr=err, check=False, timeout=600
        )

    data = _read_json(result_json) or {
        "ok": False,
        "errors": ["SPM segmentation did not produce result JSON."],
    }

    data["node_id"] = "spm_segment_subject"
    data["backend"] = "matlab-spm"
    data["subject_id"] = subject_id
    data["returncode"] = completed.returncode
    data["input_t1w"] = str(input_t1w)
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    qc_outputs = []
    if data.get("gm_file") and data.get("wm_file") and data.get("csf_file") and data.get("deformation_field"):
        qc = compute_tissue_qc_for_subject(
            subject_id=subject_id,
            gm_file=data["gm_file"],
            wm_file=data["wm_file"],
            csf_file=data["csf_file"],
            deformation_field=data["deformation_field"],
            derivatives_dir=derivatives_dir,
        )
        data["tissue_qc"] = qc
        qc_outputs = qc.get("outputs", [])

    outputs = []
    required_outputs = []
    for key in ["gm_file", "wm_file", "csf_file", "deformation_field"]:
        if data.get(key):
            outputs.append(data[key])
            required_outputs.append(data[key])

    outputs.extend(qc_outputs)
    outputs.extend([str(result_json), str(stdout_log), str(stderr_log)])
    missing_errors = missing_output_errors(required_outputs)
    if missing_errors:
        data["ok"] = False
        data.setdefault("errors", []).extend(missing_errors)

    data["outputs"] = sorted(set(outputs))
    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.segment",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(input_t1w)],
        outputs=data["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data
