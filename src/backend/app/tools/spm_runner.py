from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.backend.app.runtime.external_tool_result import (
    from_subprocess_result,
    standard_external_safety,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _matlab_path(path: str) -> str:
    """Convert path to MATLAB-compatible format with forward slashes."""
    return path.replace("\\", "/")


def run_spm_smoke_test(
    matlab_command: str,
    spm_dir: str,
    work_dir: str,
    log_dir: str,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    output_dir = Path(work_dir) / "spm_smoke_test"
    output_dir.mkdir(parents=True, exist_ok=True)

    result_json = output_dir / "result.json"
    smoothed_nii = output_dir / "smoothed.nii"

    stdout_log = log_path / "spm_smoke_test_stdout.log"
    stderr_log = log_path / "spm_smoke_test_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())
    spm_abs = str(Path(spm_dir).resolve())
    output_dir_abs = str(output_dir.resolve())
    result_json_abs = str(result_json.resolve())

    matlab_code = (
        "try; "
        f"addpath('{_matlab_quote(_matlab_path(matlab_script_path))}'); "
        f"spm_smoke_test('{_matlab_quote(_matlab_path(spm_abs))}', "
        f"'{_matlab_quote(_matlab_path(output_dir_abs))}', "
        f"'{_matlab_quote(_matlab_path(result_json_abs))}'); "
        "catch ME; disp(getReport(ME)); exit(1); end; exit(0);"
    )

    is_windows = sys.platform == "win32"

    if is_windows:
        cmd = [
            matlab_command,
            "-nodisplay",
            "-nosplash",
            "-batch",
            matlab_code,
        ]
    else:
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
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {
                "ok": False,
                "errors": [f"Failed to parse SPM smoke test JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["SPM smoke test did not produce result.json."],
        }

    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)
    data["expected_outputs"] = [str(smoothed_nii)]

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    if not smoothed_nii.exists():
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"Expected output not found: {smoothed_nii}")

    data["external_tool_result"] = from_subprocess_result(
        tool_name="spm.smoke_test",
        backend="matlab-spm",
        command=cmd,
        returncode=completed.returncode,
        inputs=[],
        outputs=[str(smoothed_nii), str(result_json), str(stdout_log), str(stderr_log)],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval={"approved": True, "required": False, "reason": "environment smoke test"},
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )
    return data


# ── M6-T004a: SPM smoke safety preflight ────────────────────────────────────

def spm_smoke_preflight(
    *,
    matlab_command: str,
    spm_dir: str | Path,
    dpabi_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run safety checks before calling MATLAB for SPM smoke test.

    Uses matlab_safety.py validators.  Errors block execution;
    warnings are advisory but do not block.

    Returns a dict with ok, safety, and preflight detail.
    Does NOT call MATLAB or subprocess.
    """
    from src.backend.app.safety.matlab_safety import (
        validate_matlab_runtime_config,
        validate_spm_runtime_config,
    )

    if dpabi_dir:
        safety_result = validate_matlab_runtime_config(
            matlab_command=matlab_command,
            spm_dir=spm_dir,
            dpabi_dir=dpabi_dir,
        )
    else:
        safety_result = validate_spm_runtime_config(
            matlab_command=matlab_command,
            spm_dir=spm_dir,
        )

    safety_errors = [e.to_dict() for e in safety_result.errors]
    safety_warnings = [w.to_dict() for w in safety_result.warnings]

    preflight_ok = len(safety_errors) == 0

    return {
        "ok": preflight_ok,
        "safety": {
            "ok": safety_result.ok,
            "errors": safety_errors,
            "warnings": safety_warnings,
        },
        "matlab_command_checked": True,
        "spm_dir_checked": True,
        "dpabi_dir_checked": dpabi_dir is not None,
        "would_call_matlab": preflight_ok,  # Only if no safety errors
        "notes": [
            "SPM smoke test preflight completed.",
            "No MATLAB was called during preflight.",
        ] if preflight_ok else [
            "SPM smoke test preflight FAILED — safety checks blocked execution.",
        ],
    }
