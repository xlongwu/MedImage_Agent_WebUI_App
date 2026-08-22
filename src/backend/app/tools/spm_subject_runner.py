from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.backend.app.runtime.external_tool_result import (
    external_tool_failure,
    from_subprocess_result,
    standard_external_safety,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start
from src.backend.app.tools.nifti_utils import prepare_nifti_for_spm


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _find_first_bold(subject_record: dict[str, Any]) -> str | None:
    sessions = subject_record.get("sessions", [])
    for session in sessions:
        for func in session.get("func", []):
            bold = func.get("bold")
            if bold:
                return bold
    return None


def run_spm_smooth_subject(
    matlab_command: str,
    spm_dir: str,
    subject_record: dict[str, Any],
    subject_id: str,
    work_dir: str,
    log_dir: str,
    derivatives_dir: str,
    matlab_script_dir: str = "./matlab",
    fwhm: list[int] | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    """DEPRECATED: Use spm_smooth_runner for standard SPM batch smoothing.
    Requires explicit approved=True for safety."""
    if not approved:
        data = {
            "ok": False, "node_id": "spm_smooth_subject", "backend": "matlab-spm",
            "subject_id": subject_id, "outputs": [],
            "errors": ["SPM subject smooth requires approved=True. This runner is deprecated; use spm_smooth_runner instead."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="spm.smooth.deprecated",
            backend="matlab-spm",
            errors=data["errors"],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data
    fwhm = fwhm or [4, 4, 4]

    bold_path = _find_first_bold(subject_record)
    if not bold_path:
        return {
            "ok": False,
            "node_id": "spm_smooth_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "errors": [f"No BOLD file found for subject: {subject_id}"],
        }

    prepared = prepare_nifti_for_spm(
        input_path=bold_path,
        output_dir=str(Path(work_dir) / "spm_inputs" / subject_id),
    )
    if not prepared.get("ok"):
        return {
            "ok": False,
            "node_id": "spm_smooth_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "errors": prepared.get("errors", []),
        }

    input_nii = prepared["prepared_path"]

    output_dir = Path(derivatives_dir).resolve() / "spm_smooth" / subject_id / "func"
    output_dir.mkdir(parents=True, exist_ok=True)

    output_nii = (output_dir / f"{subject_id}_task-rest_bold_smoothed.nii").resolve()
    result_json = (output_dir / "spm_smooth_result.json").resolve()

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / f"{subject_id}_spm_smooth_stdout.log"
    stderr_log = log_path / f"{subject_id}_spm_smooth_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())
    spm_abs = str(Path(spm_dir).resolve())
    input_nii_abs = str(Path(input_nii).resolve())
    output_nii_abs = str(output_nii.resolve())
    result_json_abs = str(result_json.resolve())

    fwhm_expr = "[" + " ".join(str(x) for x in fwhm) + "]"

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"spm_smooth_4d('{_matlab_quote(spm_abs)}', "
        f"'{_matlab_quote(input_nii_abs)}', "
        f"'{_matlab_quote(output_nii_abs)}', "
        f"'{_matlab_quote(result_json_abs)}', "
        f"{fwhm_expr}); "
        "catch ME, disp(getReport(ME)); exit(1); end; exit(0);"
    )

    # Use -batch for better Windows compatibility
    cmd = [
        matlab_command,
        "-nodisplay",
        "-nosplash",
        "-batch",
        matlab_code,
    ]

    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        completed = reject_unreviewed_process_start(
            cmd, stdout=out, stderr=err, check=False, timeout=600
        )

    if result_json.exists():
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {
                "ok": False,
                "errors": [f"Failed to parse SPM smooth result JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["SPM smooth did not produce result JSON."],
        }

    data["node_id"] = "spm_smooth_subject"
    data["backend"] = "matlab-spm"
    data["subject_id"] = subject_id
    data["input_bold"] = bold_path
    data["prepared_input"] = input_nii
    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)
    data["outputs"] = [str(output_nii)]

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    if not output_nii.exists():
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"Expected smoothed output not found: {output_nii}")

    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.smooth.deprecated",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[input_nii],
        outputs=data.get("outputs", []),
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data
