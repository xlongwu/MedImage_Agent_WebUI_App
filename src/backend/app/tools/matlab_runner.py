from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _matlab_path(path: str) -> str:
    """Convert path to MATLAB-compatible format with forward slashes."""
    return path.replace("\\", "/")


def run_matlab_check(
    matlab_command: str,
    spm_dir: str,
    dpabi_dir: str,
    output_json: str,
    log_dir: str,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    output_path = Path(output_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    stdout_log = log_path / "matlab_check_stdout.log"
    stderr_log = log_path / "matlab_check_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())
    spm_abs = str(Path(spm_dir).resolve())
    dpabi_abs = str(Path(dpabi_dir).resolve())
    output_abs = str(output_path.resolve())

    matlab_code = (
        "try; "
        f"addpath('{_matlab_quote(_matlab_path(matlab_script_path))}'); "
        f"check_environment('{_matlab_quote(_matlab_path(spm_abs))}', "
        f"'{_matlab_quote(_matlab_path(dpabi_abs))}', "
        f"'{_matlab_quote(_matlab_path(output_abs))}'); "
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

    if output_path.exists():
        try:
            data = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {
                "ok": False,
                "errors": [f"Failed to parse MATLAB output JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["MATLAB did not produce output JSON."],
        }

    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["output_json"] = str(output_path)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    return data
