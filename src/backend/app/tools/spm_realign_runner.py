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


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _prepare_bold_input(input_bold: str, subject_id: str, derivatives_dir: str) -> str:
    input_path = Path(input_bold)
    out_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    out_dir.mkdir(parents=True, exist_ok=True)

    output_path = out_dir / f"{subject_id}_bold.nii"

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

    raise RuntimeError(f"Unsupported BOLD input extension: {input_path}")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _is_safe_synthetic_input(input_bold: str) -> bool:
    normalized = str(input_bold).replace("\\", "/")
    return "examples/synthetic_bids/rawdata" in normalized


def _is_safe_slice_timing_derivative(input_bold: str, subject_id: str, derivatives_dir: str) -> bool:
    target = Path(input_bold).resolve()
    expected = (
        Path(derivatives_dir)
        / "rsfmri_preproc"
        / subject_id
        / "func"
        / f"a{subject_id}_bold.nii"
    ).resolve()
    return target == expected and target.exists()


def run_spm_realign_subject(
    matlab_command: str,
    spm_dir: str,
    subject_id: str,
    input_bold: str,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    approved: bool = False,
    matlab_script_dir: str = "./matlab",
    allow_derivative_input: bool = False,
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "spm_realign_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["SPM realignment requires approved=true."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="spm.realign",
            backend="matlab-spm",
            errors=data["errors"],
            inputs=[input_bold],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    # ── M6-T005b: MATLAB/SPM safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_spm_runtime_config

    safety_result = validate_spm_runtime_config(
        matlab_command=matlab_command,
        spm_dir=spm_dir,
    )
    if not safety_result.ok:
        return {
            "ok": False,
            "node_id": "spm_realign_subject",
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

    safe_synthetic = _is_safe_synthetic_input(input_bold)
    safe_derivative = (
        allow_derivative_input
        and _is_safe_slice_timing_derivative(input_bold, subject_id, derivatives_dir)
    )

    if not safe_synthetic and not safe_derivative:
        return {
            "ok": False,
            "node_id": "spm_realign_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                "Refusing to run SPM realignment on unsafe input.",
                f"Input was: {input_bold}",
            ],
        }

    out_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / f"{subject_id}_spm_realign_stdout.log"
    stderr_log = log_path / f"{subject_id}_spm_realign_stderr.log"
    result_json = out_dir / "spm_realign_result.json"

    if safe_derivative:
        prepared_input = input_bold
    else:
        try:
            prepared_input = _prepare_bold_input(
                input_bold=input_bold,
                subject_id=subject_id,
                derivatives_dir=derivatives_dir,
            )
        except Exception as exc:
            return {
                "ok": False,
                "node_id": "spm_realign_subject",
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
        f"spm_realign_wrapper('{_matlab_quote(str(Path(spm_dir).resolve()))}', "
        f"'{_matlab_quote(str(Path(prepared_input).resolve()))}', "
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
        "errors": ["SPM realignment did not produce result JSON."],
    }

    data["node_id"] = "spm_realign_subject"
    data["backend"] = "matlab-spm"
    data["subject_id"] = subject_id
    data["returncode"] = completed.returncode
    data["prepared_input"] = prepared_input
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    outputs = list(data.get("realigned_files", []))
    required_outputs = list(data.get("realigned_files", []))
    if data.get("mean_file"):
        outputs.append(data["mean_file"])
        required_outputs.append(data["mean_file"])
    if data.get("motion_parameter_file"):
        outputs.append(data["motion_parameter_file"])
        required_outputs.append(data["motion_parameter_file"])
    outputs.extend([str(result_json), str(stdout_log), str(stderr_log)])
    missing_errors = missing_output_errors(required_outputs)
    if missing_errors:
        data["ok"] = False
        data.setdefault("errors", []).extend(missing_errors)

    data["outputs"] = sorted(set(outputs))
    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.realign",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[prepared_input],
        outputs=data["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data
