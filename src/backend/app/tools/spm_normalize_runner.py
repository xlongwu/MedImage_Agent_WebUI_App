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
from src.backend.app.tools.normalization_qc import compute_normalization_qc_for_subject


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _expected_deformation_field(subject_id: str, derivatives_dir: str) -> Path:
    return (
        Path(derivatives_dir)
        / "rsfmri_preproc"
        / subject_id
        / "anat"
        / f"y_coreg_{subject_id}_T1w.nii"
    )


def _find_realign_functional(subject_id: str, derivatives_dir: str) -> Path | None:
    func_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    if not func_dir.exists():
        return None

    preferred = func_dir / f"ra{subject_id}_bold.nii"
    if preferred.exists():
        return preferred

    candidates = []
    for path in sorted(func_dir.glob("r*.nii")):
        name = path.name
        if name.startswith("rp_"):
            continue
        if name.startswith("mean"):
            continue
        if name.startswith("wr"):
            continue
        candidates.append(path)

    return candidates[0] if candidates else None


def _find_mean_functional(subject_id: str, derivatives_dir: str) -> Path | None:
    func_dir = Path(derivatives_dir) / "rsfmri_preproc" / subject_id / "func"
    if not func_dir.exists():
        return None

    candidates = sorted(func_dir.glob("mean*.nii"))
    return candidates[0] if candidates else None


def _is_safe_functional_input(path: Path, subject_id: str, derivatives_dir: str) -> bool:
    func_dir = (
        Path(derivatives_dir)
        / "rsfmri_preproc"
        / subject_id
        / "func"
    ).resolve()

    try:
        path.resolve().relative_to(func_dir)
    except ValueError:
        return False

    name = path.name
    return (
        name.startswith("r")
        and name.endswith(".nii")
        and not name.startswith("rp_")
        and not name.startswith("mean")
        and not name.startswith("wr")
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_spm_normalize_subject(
    matlab_command: str,
    spm_dir: str,
    subject_id: str,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    approved: bool = False,
    voxel_size: list[float] | None = None,
    bounding_box: list[list[float]] | None = None,
    normalize_mean: bool = True,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "spm_normalize_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["SPM normalization requires approved=true."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="spm.normalize",
            backend="matlab-spm",
            errors=data["errors"],
            inputs=[],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    # ── M6-T009b: MATLAB/SPM safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_spm_runtime_config
    safety_result = validate_spm_runtime_config(matlab_command=matlab_command, spm_dir=spm_dir)
    if not safety_result.ok:
        return {'ok': False, 'node_id': 'spm_normalize_subject', 'backend': 'matlab-spm',
                'subject_id': subject_id, 'outputs': [], 'warnings': [],
                'errors': [f'MATLAB/SPM safety preflight failed: {e.message}' for e in safety_result.errors],
                'safety': safety_result.to_dict(), 'matlab_called': False, 'spm_called': False,
                'stage': 'matlab_safety_preflight'}

    voxel_size = voxel_size or [3.0, 3.0, 3.0]
    bounding_box = bounding_box or [[-90.0, -126.0, -72.0], [90.0, 90.0, 108.0]]

    deformation_field = _expected_deformation_field(subject_id, derivatives_dir)
    if not deformation_field.exists():
        return {
            "ok": False,
            "node_id": "spm_normalize_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Expected deformation field not found: {deformation_field}"],
        }

    input_func = _find_realign_functional(subject_id, derivatives_dir)
    if not input_func:
        return {
            "ok": False,
            "node_id": "spm_normalize_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                f"No realigned functional input found under derivatives/rsfmri_preproc/{subject_id}/func."
            ],
        }

    if not _is_safe_functional_input(input_func, subject_id, derivatives_dir):
        return {
            "ok": False,
            "node_id": "spm_normalize_subject",
            "backend": "matlab-spm",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Unsafe normalization functional input: {input_func}"],
        }

    mean_func = _find_mean_functional(subject_id, derivatives_dir)
    mean_func_text = str(mean_func) if mean_func else ""

    func_dir = input_func.parent

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / f"{subject_id}_spm_normalize_stdout.log"
    stderr_log = log_path / f"{subject_id}_spm_normalize_stderr.log"
    result_json = func_dir / "spm_normalization_result.json"

    matlab_script_path = str(Path(matlab_script_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"spm_normalize_write_wrapper('{_matlab_quote(str(Path(spm_dir).resolve()))}', "
        f"'{_matlab_quote(str(deformation_field.resolve()))}', "
        f"'{_matlab_quote(str(input_func.resolve()))}', "
        f"'{str(bool(normalize_mean)).lower()}', "
        f"'{_matlab_quote(str(Path(mean_func_text).resolve()) if mean_func_text else '')}', "
        f"'{_matlab_quote(json.dumps(voxel_size))}', "
        f"'{_matlab_quote(json.dumps(bounding_box))}', "
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
        "errors": ["SPM normalization did not produce result JSON."],
    }

    data["node_id"] = "spm_normalize_subject"
    data["backend"] = "matlab-spm"
    data["subject_id"] = subject_id
    data["returncode"] = completed.returncode
    data["input_func"] = str(input_func)
    data["deformation_field"] = str(deformation_field)
    data["mean_func"] = mean_func_text
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    qc_outputs = []
    normalized_file = data.get("normalized_file")

    if normalized_file:
        qc = compute_normalization_qc_for_subject(
            subject_id=subject_id,
            input_nii=str(input_func),
            deformation_field=str(deformation_field),
            normalized_nii=normalized_file,
            derivatives_dir=derivatives_dir,
            target_voxel_size=voxel_size,
        )
        data["normalization_qc"] = qc
        qc_outputs = qc.get("outputs", [])

    outputs = []
    required_outputs = []
    if data.get("normalized_file"):
        outputs.append(data["normalized_file"])
        required_outputs.append(data["normalized_file"])
    if data.get("normalized_mean_file"):
        outputs.append(data["normalized_mean_file"])
        required_outputs.append(data["normalized_mean_file"])

    outputs.extend(qc_outputs)
    outputs.extend([str(result_json), str(stdout_log), str(stderr_log)])
    missing_errors = missing_output_errors(required_outputs)
    if missing_errors:
        data["ok"] = False
        data.setdefault("errors", []).extend(missing_errors)

    data["outputs"] = sorted(set(outputs))
    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.normalize",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(deformation_field), str(input_func)],
        outputs=data["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": approved, "required": True},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data
