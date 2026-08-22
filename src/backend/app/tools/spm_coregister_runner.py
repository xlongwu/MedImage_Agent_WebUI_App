from __future__ import annotations

import json
import shutil
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
from src.backend.app.tools.registration_qc import compute_registration_qc_for_subject


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _find_subject_t1w(subject_record: dict[str, Any]) -> str | None:
    for session in subject_record.get("sessions", []):
        anat = session.get("anat", {})
        if isinstance(anat, dict) and anat.get("t1w"):
            return anat.get("t1w")

        if isinstance(anat, list):
            for item in anat:
                if item.get("t1w"):
                    return item.get("t1w")

    if subject_record.get("anat"):
        anat = subject_record.get("anat")
        if isinstance(anat, dict) and anat.get("t1w"):
            return anat.get("t1w")

    return None


def _find_mean_functional(subject_id: str, derivatives_dir: str) -> str | None:
    func_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    if not func_dir.exists():
        return None

    candidates = sorted(func_dir.glob("mean*.nii"))
    return str(candidates[0]) if candidates else None


def _is_safe_synthetic_t1w(path: str) -> bool:
    normalized = str(path).replace("\\", "/")
    return "examples/synthetic_bids/rawdata" in normalized and (
        normalized.endswith(".nii") or normalized.endswith(".nii.gz")
    )


def _prepare_t1w_input(input_t1w: str, subject_id: str, derivatives_dir: str) -> str:
    input_path = Path(input_t1w)
    out_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "anat"
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{subject_id}_T1w.nii"

    if input_path.name.endswith(".nii"):
        shutil.copyfile(input_path, output_path)
        return str(output_path)

    if input_path.name.endswith(".nii.gz"):
        try:
            import nibabel as nib
        except ImportError as exc:
            raise RuntimeError("Missing dependency: nibabel. Install with: pip install nibabel") from exc

        img = nib.load(str(input_path))
        nib.save(img, str(output_path))
        return str(output_path)

    raise RuntimeError(f"Unsupported T1w input extension: {input_path}")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_spm_coregister_subject(
    matlab_command: str,
    spm_dir: str,
    subject_id: str,
    subject_record: dict[str, Any],
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    approved: bool = False,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "spm_coregister_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["SPM coregistration requires approved=true."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="spm.coregister",
            backend="matlab-spm",
            errors=data["errors"],
            inputs=[],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    # ── M6-T007b: MATLAB/SPM safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_spm_runtime_config

    safety_result = validate_spm_runtime_config(
        matlab_command=matlab_command,
        spm_dir=spm_dir,
    )
    if not safety_result.ok:
        return {
            "ok": False,
            "node_id": "spm_coregister_subject",
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

    input_t1w = _find_subject_t1w(subject_record)
    if not input_t1w:
        return {
            "ok": False,
            "node_id": "spm_coregister_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["No T1w input found for subject."],
        }

    if not _is_safe_synthetic_t1w(input_t1w):
        return {
            "ok": False,
            "node_id": "spm_coregister_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                "Refusing to run SPM coregistration on non-synthetic T1w input.",
                f"Input was: {input_t1w}",
            ],
        }

    reference_nii = _find_mean_functional(subject_id, derivatives_dir)
    if not reference_nii:
        return {
            "ok": False,
            "node_id": "spm_coregister_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                "Mean functional reference image not found.",
                f"Expected under derivatives/rsfmri_preproc/{subject_id}/func/mean*.nii",
            ],
        }

    anat_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "anat"
    anat_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / f"{subject_id}_spm_coregister_stdout.log"
    stderr_log = log_path / f"{subject_id}_spm_coregister_stderr.log"
    result_json = anat_dir / "spm_coregistration_result.json"

    try:
        prepared_t1w = _prepare_t1w_input(
            input_t1w=input_t1w,
            subject_id=subject_id,
            derivatives_dir=derivatives_dir,
        )
    except Exception as exc:
        return {
            "ok": False,
            "node_id": "spm_coregister_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [str(exc)],
        }

    matlab_script_path = str(Path(matlab_script_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"spm_coregister_wrapper('{_matlab_quote(str(Path(spm_dir).resolve()))}', "
        f"'{_matlab_quote(str(Path(reference_nii).resolve()))}', "
        f"'{_matlab_quote(str(Path(prepared_t1w).resolve()))}', "
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
        "errors": ["SPM coregistration did not produce result JSON."],
    }

    data["node_id"] = "spm_coregister_subject"
    data["backend"] = "matlab-spm"
    data["subject_id"] = subject_id
    data["returncode"] = completed.returncode
    data["reference_nii"] = reference_nii
    data["prepared_t1w"] = prepared_t1w
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    coregistered = data.get("coregistered_file")

    qc_outputs = []
    if coregistered:
        qc = compute_registration_qc_for_subject(
            subject_id=subject_id,
            reference_nii=reference_nii,
            source_nii=prepared_t1w,
            coregistered_nii=coregistered,
            derivatives_dir=derivatives_dir,
        )
        data["registration_qc"] = qc
        qc_outputs = qc.get("outputs", [])

    outputs = []
    required_outputs = []
    if coregistered:
        outputs.append(coregistered)
        required_outputs.append(coregistered)
    outputs.extend(qc_outputs)
    outputs.extend([str(result_json), str(stdout_log), str(stderr_log)])
    missing_errors = missing_output_errors(required_outputs)
    if missing_errors:
        data["ok"] = False
        data.setdefault("errors", []).extend(missing_errors)

    data["outputs"] = sorted(set(outputs))
    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.coregister",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[reference_nii, prepared_t1w],
        outputs=data["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data
