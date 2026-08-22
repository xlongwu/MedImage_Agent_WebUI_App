from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.runtime.external_tool_result import (
    external_tool_failure,
    from_subprocess_result,
    standard_external_safety,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start
from src.backend.app.tools.dpabi_function_contracts import get_dpabi_single_function_contract
from src.backend.app.tools.dpabi_safety import ALLOWED_FUNCTIONS as ALLOWLISTED_SINGLE_FUNCTIONS


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def run_dpabi_single_function_sandbox(
    matlab_command: str,
    dpabi_dir: str,
    work_dir: str,
    log_dir: str,
    function_name: str = "y_Smooth",
    approved: bool = False,
    approved_by: str = "local-user",
    matlab_script_dir: str = "./matlab",
    spm_dir: str = "./third_party/spm12",
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "dpabi_single_function_sandbox",
            "backend": "matlab-dpabi",
            "function_name": function_name,
            "outputs": [],
            "warnings": [],
            "errors": ["DPABI single-function sandbox requires approved=true."],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name=f"dpabi.{function_name}",
            backend="matlab-dpabi",
            errors=data["errors"],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    if function_name not in ALLOWLISTED_SINGLE_FUNCTIONS:
        return {
            "ok": False,
            "node_id": "dpabi_single_function_sandbox",
            "backend": "matlab-dpabi",
            "function_name": function_name,
            "outputs": [],
            "warnings": [],
            "errors": [f"Function is not allowlisted: {function_name}"],
        }

    dpabi_work = Path(work_dir) / "dpabi"
    sandbox_dir = dpabi_work / "single_function_sandbox"
    audit_dir = dpabi_work / "audit"
    approvals_dir = dpabi_work / "approvals"
    report_dir = Path("outputs/reports") / "dpabi"
    log_path = Path(log_dir)

    sandbox_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    approvals_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path.mkdir(parents=True, exist_ok=True)

    contracts_path = dpabi_work / "dpabi_wrapper_contracts.json"
    contracts = _read_json(contracts_path)

    if not contracts:
        return {
            "ok": False,
            "node_id": "dpabi_single_function_sandbox",
            "backend": "matlab-dpabi",
            "function_name": function_name,
            "outputs": [],
            "warnings": [],
            "errors": [f"Missing wrapper contracts: {contracts_path}"],
        }

    contract = _find_contract(contracts, function_name)
    function_contract = get_dpabi_single_function_contract(function_name)
    if not contract:
        return {
            "ok": False,
            "node_id": "dpabi_single_function_sandbox",
            "backend": "matlab-dpabi",
            "function_name": function_name,
            "outputs": [],
            "warnings": [],
            "errors": [f"No wrapper contract found for function: {function_name}"],
        }

    if not contract.get("wrapper_candidate"):
        return {
            "ok": False,
            "node_id": "dpabi_single_function_sandbox",
            "backend": "matlab-dpabi",
            "function_name": function_name,
            "outputs": [],
            "warnings": [],
            "errors": [
                f"Function is not marked as wrapper_candidate: {function_name}. "
                f"Reason: {contract.get('blocked_reason')}"
            ],
        }

    # ── M7-DPABI-T005b: DPABI runtime safety preflight ──
    from src.backend.app.safety.matlab_safety import validate_matlab_runtime_config
    sr = validate_matlab_runtime_config(matlab_command=matlab_command, spm_dir=spm_dir, dpabi_dir=dpabi_dir)
    if not sr.ok:
        errors = [f'DPABI runtime safety preflight failed: {e.message}' for e in sr.errors]
        data = {'ok': False, 'node_id': 'dpabi_single_function_sandbox', 'backend': 'matlab-dpabi',
                'function_name': function_name, 'outputs': [], 'warnings': [],
                'errors': errors, 'safety': sr.to_dict(),
                'matlab_called': False, 'dpabi_called': False, 'stage': 'dpabi_runtime_preflight'}
        data['external_tool_result'] = external_tool_failure(
            tool_name=f'dpabi.{function_name}', backend='matlab-dpabi',
            errors=errors, inputs=[], approval={'approved': approved, 'required': True},
            safety=standard_external_safety(),
        )
        return data

    approval_record = {
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
        "execution_type": "dpabi_single_function_sandbox",
        "function_name": function_name,
        "full_dpabi_execution": False,
        "dpabi_gui_called": False,
        "dparsf_run_called": False,
        "rawdata_modified": False,
        "files_deleted": False,
        "contracts_path": str(contracts_path),
    }

    approval_path = approvals_dir / "dpabi_single_function_approval.json"
    approval_path.write_text(
        json.dumps(approval_record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result_json = sandbox_dir / "dpabi_single_function_result.json"
    stdout_log = log_path / "dpabi_single_function_stdout.log"
    stderr_log = log_path / "dpabi_single_function_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"dpabi_single_function_sandbox('{_matlab_quote(str(Path(dpabi_dir).resolve()))}', "
        f"'{_matlab_quote(function_name)}', "
        f"'{_matlab_quote(str(sandbox_dir.resolve()))}', "
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
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            data = {
                "ok": False,
                "errors": [f"Failed to parse single-function result JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["Single-function sandbox did not produce result JSON."],
        }

    data["node_id"] = "dpabi_single_function_sandbox"
    data["backend"] = "matlab-dpabi"
    data["function_name"] = function_name
    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)
    data["approval_record"] = str(approval_path)
    data["contract"] = function_contract or contract

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")
    elif not data.get("outputs"):
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append("DPABI sandbox completed but did not report output artifacts.")

    audit = {
        "ok": bool(data.get("ok")),
        "execution_type": "dpabi_single_function_sandbox",
        "function_name": function_name,
        "full_dpabi_execution": False,
        "dpabi_gui_called": False,
        "dparsf_run_called": False,
        "rawdata_modified": False,
        "files_deleted": False,
        "approval_record": str(approval_path),
        "contracts_path": str(contracts_path),
        "result_json": str(result_json),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "returncode": completed.returncode,
        "contract": contract,
        "errors": data.get("errors", []),
        "warnings": data.get("warnings", []),
        "metrics": data.get("metrics", {}),
    }

    audit_json = audit_dir / "dpabi_single_function_wrapper_audit.json"
    audit_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report_path = report_dir / "dpabi_single_function_wrapper_report.md"
    lines = []
    lines.append("# DPABI Single-Function Wrapper Sandbox Report")
    lines.append("")
    lines.append(f"- OK: {audit['ok']}")
    lines.append(f"- Function: {function_name}")
    lines.append(f"- Full DPABI execution: {audit['full_dpabi_execution']}")
    lines.append(f"- DPABI GUI called: {audit['dpabi_gui_called']}")
    lines.append(f"- DPARSF_run called: {audit['dparsf_run_called']}")
    lines.append(f"- Rawdata modified: {audit['rawdata_modified']}")
    lines.append(f"- Files deleted: {audit['files_deleted']}")
    lines.append(f"- Return code: {audit['returncode']}")
    lines.append("")
    lines.append("## Contract")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(contract, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(audit.get("metrics", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by MedImage Agent - DPABI Single-Function Wrapper Sandbox*")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    data["audit_json"] = str(audit_json)
    data["audit_report"] = str(report_path)
    data["outputs"] = data.get("outputs", []) + [str(audit_json), str(report_path)]
    data["external_tool_result"] = from_subprocess_result(
        tool_name=f"dpabi.{function_name}",
        backend="matlab-dpabi",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(contracts_path)],
        outputs=data["outputs"],
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval=approval_record,
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )

    return data
