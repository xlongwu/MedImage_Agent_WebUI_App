from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def run_dpabi_signature_probe(
    matlab_command: str,
    dpabi_dir: str,
    work_dir: str,
    log_dir: str,
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    output_dir = Path(work_dir) / "dpabi"
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    output_json = output_dir / "dpabi_function_signatures.json"
    stdout_log = log_path / "dpabi_signature_probe_stdout.log"
    stderr_log = log_path / "dpabi_signature_probe_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())
    dpabi_abs = str(Path(dpabi_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"dpabi_signature_probe('{_matlab_quote(dpabi_abs)}', "
        f"'{_matlab_quote(str(output_json.resolve()))}'); "
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

    if output_json.exists():
        try:
            data = json.loads(output_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {
                "ok": False,
                "errors": [f"Failed to parse DPABI signature JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["DPABI signature probe did not produce output JSON."],
        }

    data["node_id"] = "dpabi_signature_probe"
    data["backend"] = "matlab-dpabi"
    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(output_json)
    data["outputs"] = [str(output_json)]

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    return data
