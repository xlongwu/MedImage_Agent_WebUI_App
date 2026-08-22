from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.backend.app.tools.dpabi_safety import ALLOWED_FUNCTIONS as ALLOWLISTED_SINGLE_FUNCTIONS
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_contract(contracts: dict[str, Any], function_name: str) -> dict[str, Any] | None:
    for item in contracts.get("contracts", []):
        if item.get("function_name") == function_name:
            return item
    return None


def _prepare_input_nifti(input_bold: str, subject_id: str, work_dir: str) -> str:
    input_path = Path(input_bold)
    workspace = Path(work_dir) / "dpabi" / "subject_wrapper_workspace" / subject_id
    workspace.mkdir(parents=True, exist_ok=True)

    output_path = workspace / "input.nii"

    if input_path.suffix == ".nii":
        try:
            import shutil
            shutil.copyfile(input_path, output_path)
            return str(output_path)
        except Exception as exc:
            raise RuntimeError(f"Failed to copy input NIfTI: {exc}") from exc

    try:
        import nibabel as nib
    except ImportError as exc:
        raise RuntimeError("Missing dependency: nibabel. Install with: pip install nibabel") from exc

    img = nib.load(str(input_path))
    nib.save(img, str(output_path))
    return str(output_path)


def run_dpabi_subject_smooth(
    matlab_command: str,
    dpabi_dir: str,
    subject_id: str,
    input_bold: str,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    function_name: str = "y_Smooth",
    fwhm: list[float] | None = None,
    approved: bool = False,
    matlab_script_dir: str = "./matlab",
    spm_dir: str = "./third_party/spm12",
) -> dict[str, Any]:
    if not approved:
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": ["DPABI subject wrapper requires approved=true."],
        }

    if function_name not in ALLOWLISTED_SINGLE_FUNCTIONS:
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Function is not allowlisted: {function_name}"],
        }

    normalized_input = str(input_bold).replace("\\", "/")
    if "examples/synthetic_bids/rawdata" not in normalized_input:
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                "Refusing to run DPABI subject wrapper on non-synthetic input. "
                f"Input was: {input_bold}"
            ],
        }

    # ── M7-DPABI-T006b: FWHM validation ──
    fwhm = fwhm or [6.0, 6.0, 6.0]
    if not isinstance(fwhm, list | tuple) or len(fwhm) != 3:
        return {'ok': False, 'node_id': 'dpabi_subject_smooth', 'backend': 'matlab-dpabi',
                'subject_id': subject_id, 'outputs': [], 'warnings': [],
                'errors': ['FWHM must be a 3-element list of numbers.'],
                'matlab_called': False, 'dpabi_called': False, 'stage': 'fwhm_preflight'}
    for i, v in enumerate(fwhm):
        if not isinstance(v, int | float) or v != v or v == float('inf') or v <= 0 or v > 12:
            return {'ok': False, 'node_id': 'dpabi_subject_smooth', 'backend': 'matlab-dpabi',
                    'subject_id': subject_id, 'outputs': [], 'warnings': [],
                    'errors': [f'FWHM element {i} invalid: {v}. Must be 0 < value <= 12.'],
                    'matlab_called': False, 'dpabi_called': False, 'stage': 'fwhm_preflight'}

    # ── M7-DPABI-T006b: DPABI runtime safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_matlab_runtime_config
    sr = validate_matlab_runtime_config(matlab_command=matlab_command, spm_dir=spm_dir, dpabi_dir=dpabi_dir)
    if not sr.ok:
        errors = [f'DPABI runtime safety preflight failed: {e.message}' for e in sr.errors]
        data = {'ok': False, 'node_id': 'dpabi_subject_smooth', 'backend': 'matlab-dpabi',
                'subject_id': subject_id, 'outputs': [], 'warnings': [],
                'errors': errors, 'safety': sr.to_dict(),
                'matlab_called': False, 'dpabi_called': False, 'stage': 'dpabi_runtime_preflight'}
        return data

    contracts_path = Path(work_dir) / "dpabi" / "dpabi_wrapper_contracts.json"
    contracts = _read_json(contracts_path)
    if not contracts:
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [f"Missing wrapper contracts: {contracts_path}"],
        }

    contract = _find_contract(contracts, function_name)
    if not contract or not contract.get("wrapper_candidate"):
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [
                f"Function is not a wrapper candidate: {function_name}. "
                f"Contract: {contract}"
            ],
        }

    fwhm = fwhm or [4, 4, 4]

    out_dir = Path(derivatives_dir) / "dpabi_single_function" / subject_id / "func"
    out_dir.mkdir(parents=True, exist_ok=True)

    output_nii = out_dir / f"{subject_id}_dpabi_smooth.nii"
    result_json = out_dir / "dpabi_subject_wrapper_result.json"

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    stdout_log = log_path / f"{subject_id}_dpabi_subject_wrapper_stdout.log"
    stderr_log = log_path / f"{subject_id}_dpabi_subject_wrapper_stderr.log"

    try:
        prepared_input = _prepare_input_nifti(input_bold, subject_id, work_dir)
    except Exception as exc:
        return {
            "ok": False,
            "node_id": "dpabi_subject_smooth",
            "backend": "matlab-dpabi",
            "subject_id": subject_id,
            "outputs": [],
            "warnings": [],
            "errors": [str(exc)],
        }

    matlab_script_path = str(Path(matlab_script_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"dpabi_subject_smooth_wrapper('{_matlab_quote(str(Path(dpabi_dir).resolve()))}', "
        f"'{_matlab_quote(function_name)}', "
        f"'{_matlab_quote(str(Path(prepared_input).resolve()))}', "
        f"'{_matlab_quote(str(output_nii.resolve()))}', "
        f"'{_matlab_quote(json.dumps(fwhm))}', "
        f"'{_matlab_quote(str(result_json.resolve()))}'); "
        "catch ME, disp(getReport(ME)); exit(1); end; exit(0);"
    )

    cmd = [
        matlab_command,
        "-nodisplay",
        "-nosplash",
        "-r",
        matlab_code,
    ]

    with stdout_log.open("w", encoding="utf-8") as out, stderr_log.open("w", encoding="utf-8") as err:
        completed = reject_unreviewed_process_start(
            cmd, stdout=out, stderr=err, check=False
        )

    if result_json.exists():
        data = _read_json(result_json) or {
            "ok": False,
            "errors": ["Failed to parse DPABI subject wrapper result JSON."],
        }
    else:
        data = {
            "ok": False,
            "errors": ["DPABI subject wrapper did not produce result JSON."],
        }

    data["node_id"] = "dpabi_subject_smooth"
    data["backend"] = "matlab-dpabi"
    data["subject_id"] = subject_id
    data["function_name"] = function_name
    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)
    data["prepared_input"] = prepared_input

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    outputs = list(data.get("outputs", []))
    outputs.extend([str(output_nii), str(result_json)])
    data["outputs"] = sorted(set(outputs))

    return data
