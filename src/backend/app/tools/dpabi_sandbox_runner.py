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


def _matlab_quote(value: str) -> str:
    return value.replace("'", "''")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_dpabi_sandbox_smoke(
    matlab_command: str,
    dpabi_dir: str,
    work_dir: str,
    log_dir: str,
    approved: bool,
    approved_by: str = "local-user",
    matlab_script_dir: str = "./matlab",
) -> dict[str, Any]:
    if not approved:
        data = {
            "ok": False,
            "node_id": "dpabi_sandbox_smoke_run",
            "backend": "matlab-dpabi",
            "outputs": [],
            "errors": ["DPABI sandbox smoke run requires approved=true."],
            "warnings": [],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="dpabi.sandbox_smoke",
            backend="matlab-dpabi",
            errors=data["errors"],
            approval={"approved": False, "required": True},
            safety=standard_external_safety(),
        )
        return data

    dpabi_work = Path(work_dir) / "dpabi"
    sandbox_dir = dpabi_work / "sandbox"
    approvals_dir = dpabi_work / "approvals"
    audit_dir = dpabi_work / "audit"
    report_dir = Path("outputs/reports") / "dpabi"
    log_path = Path(log_dir)

    sandbox_dir.mkdir(parents=True, exist_ok=True)
    approvals_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path.mkdir(parents=True, exist_ok=True)

    run_plan_path = dpabi_work / "dpabi_run_plan.json"
    run_plan = _read_json(run_plan_path)

    if not run_plan:
        data = {
            "ok": False,
            "node_id": "dpabi_sandbox_smoke_run",
            "backend": "matlab-dpabi",
            "outputs": [],
            "errors": [f"Missing DPABI run plan: {run_plan_path}"],
            "warnings": [],
        }
        data["external_tool_result"] = external_tool_failure(
            tool_name="dpabi.sandbox_smoke",
            backend="matlab-dpabi",
            errors=data["errors"],
            inputs=[str(run_plan_path)],
            approval={"approved": approved, "approved_by": approved_by, "required": True},
            safety=standard_external_safety(),
        )
        return data

    # ── M7-DPABI-T004b: DPABI runtime safety preflight (runs AFTER plan check, BEFORE subprocess) ──
    from src.backend.app.safety.matlab_safety import (
        validate_matlab_command,
        validate_third_party_dir,
    )
    _cmd_sr = validate_matlab_command(matlab_command)
    _dpabi_sr = validate_third_party_dir(dpabi_dir, name="dpabi_dir")
    from src.backend.app.safety.matlab_safety import MatlabSafetyResult
    sr = MatlabSafetyResult(
        ok=len(_cmd_sr.errors) + len(_dpabi_sr.errors) == 0,
        errors=list(_cmd_sr.errors) + list(_dpabi_sr.errors),
        warnings=list(_cmd_sr.warnings) + list(_dpabi_sr.warnings),
    )
    if not sr.ok:
        errors = [f'DPABI runtime safety preflight failed: {e.message}' for e in sr.errors]
        data = {'ok': False, 'node_id': 'dpabi_sandbox_smoke_run', 'backend': 'matlab-dpabi',
                'outputs': [], 'warnings': [],
                'errors': errors,
                'safety': sr.to_dict(), 'matlab_called': False, 'dpabi_called': False,
                'stage': 'dpabi_runtime_preflight'}
        data['external_tool_result'] = external_tool_failure(
            tool_name='dpabi.sandbox_smoke', backend='matlab-dpabi',
            errors=errors, inputs=[str(run_plan_path)], approval={'approved': approved, 'required': True},
            safety=standard_external_safety(),
        )
        return data

    approval_record = {
        "approved": True,
        "approved_by": approved_by,
        "approved_at": _now_iso(),
        "execution_type": "dpabi_sandbox_smoke_run",
        "full_dpabi_execution": False,
        "dpabi_gui_called": False,
        "dparsf_run_called": False,
        "rawdata_modified": False,
        "files_deleted": False,
        "run_plan_path": str(run_plan_path),
    }

    approval_path = approvals_dir / "dpabi_sandbox_smoke_approval.json"
    approval_path.write_text(
        json.dumps(approval_record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result_json = sandbox_dir / "dpabi_sandbox_smoke_result.json"
    stdout_log = log_path / "dpabi_sandbox_smoke_stdout.log"
    stderr_log = log_path / "dpabi_sandbox_smoke_stderr.log"

    matlab_script_path = str(Path(matlab_script_dir).resolve())
    dpabi_abs = str(Path(dpabi_dir).resolve())
    sandbox_abs = str(sandbox_dir.resolve())
    result_abs = str(result_json.resolve())

    matlab_code = (
        "try, "
        f"addpath('{_matlab_quote(matlab_script_path)}'); "
        f"dpabi_sandbox_smoke_run('{_matlab_quote(dpabi_abs)}', "
        f"'{_matlab_quote(sandbox_abs)}', "
        f"'{_matlab_quote(result_abs)}'); "
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
                "errors": [f"Failed to parse sandbox smoke result JSON: {exc}"],
            }
    else:
        data = {
            "ok": False,
            "errors": ["DPABI sandbox smoke run did not produce result JSON."],
        }

    data["node_id"] = "dpabi_sandbox_smoke_run"
    data["backend"] = "matlab-dpabi"
    data["returncode"] = completed.returncode
    data["stdout_log"] = str(stdout_log)
    data["stderr_log"] = str(stderr_log)
    data["result_json"] = str(result_json)
    data["approval_record"] = str(approval_path)

    if completed.returncode != 0:
        data["ok"] = False
        data.setdefault("errors", [])
        data["errors"].append(f"MATLAB exited with return code {completed.returncode}.")

    audit = {
        "ok": bool(data.get("ok")),
        "execution_type": "dpabi_sandbox_smoke_run",
        "full_dpabi_execution": False,
        "dpabi_gui_called": False,
        "dparsf_run_called": False,
        "rawdata_modified": False,
        "files_deleted": False,
        "approval_record": str(approval_path),
        "run_plan_path": str(run_plan_path),
        "result_json": str(result_json),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "returncode": completed.returncode,
        "errors": data.get("errors", []),
        "warnings": data.get("warnings", []),
        "metrics": data.get("metrics", {}),
    }

    audit_json = audit_dir / "dpabi_sandbox_execution_audit.json"
    audit_json.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    audit_md = report_dir / "dpabi_sandbox_execution_audit.md"
    lines = []
    lines.append("# DPABI Sandbox Execution Audit")
    lines.append("")
    lines.append(f"- OK: {audit['ok']}")
    lines.append(f"- Execution type: {audit['execution_type']}")
    lines.append(f"- Full DPABI execution: {audit['full_dpabi_execution']}")
    lines.append(f"- DPABI GUI called: {audit['dpabi_gui_called']}")
    lines.append(f"- DPARSF_run called: {audit['dparsf_run_called']}")
    lines.append(f"- Rawdata modified: {audit['rawdata_modified']}")
    lines.append(f"- Files deleted: {audit['files_deleted']}")
    lines.append(f"- Return code: {audit['returncode']}")
    lines.append(f"- Result JSON: `{result_json}`")
    lines.append(f"- Approval record: `{approval_path}`")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(audit.get("metrics", {}), ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Errors")
    lines.append("")
    if audit["errors"]:
        for item in audit["errors"]:
            lines.append(f"- {item}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Safety Note")
    lines.append("")
    lines.append("This was a sandbox smoke run only. It did not run full DPABI preprocessing.")

    audit_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    outputs = list(data.get("outputs", []))
    outputs.extend([str(result_json), str(approval_path), str(audit_json), str(audit_md)])

    data["outputs"] = outputs
    data["audit_json"] = str(audit_json)
    data["audit_report"] = str(audit_md)
    data["external_tool_result"] = from_subprocess_result(
        tool_name="dpabi.sandbox_smoke",
        backend="matlab-dpabi",
        command=cmd,
        returncode=completed.returncode,
        inputs=[str(run_plan_path)],
        outputs=outputs,
        logs={"stdout": str(stdout_log), "stderr": str(stderr_log), "result_json": str(result_json)},
        approval=approval_record,
        safety=standard_external_safety(),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )

    return data
