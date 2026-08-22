"""SPM chain validator — orchestrate full 6-node SPM preprocessing chain."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start

CHAIN_NODES = [
    {
        "node_id": "spm_slice_timing",
        "description": "Slice Timing Correction",
        "runner": "backend.app.tools.spm_slice_timing_runner",
        "func": "run_spm_slice_timing_subject",
        "output_bold_pattern": "a",
    },
    {
        "node_id": "spm_realign",
        "description": "Realignment",
        "runner": "backend.app.tools.spm_realign_runner",
        "func": "run_spm_realign_subject",
        "output_bold_pattern": "r",
    },
    {
        "node_id": "spm_coregister",
        "description": "Coregistration",
        "runner": "backend.app.tools.spm_coregister_runner",
        "func": "run_spm_coregister_subject",
        "output_bold_pattern": "coreg_",
    },
    {
        "node_id": "spm_segment",
        "description": "Segmentation",
        "runner": "backend.app.tools.spm_segment_runner",
        "func": "run_spm_segment_subject",
        "output_bold_pattern": "c1",
    },
    {
        "node_id": "spm_normalize",
        "description": "Normalization",
        "runner": "backend.app.tools.spm_normalize_runner",
        "func": "run_spm_normalize_subject",
        "output_bold_pattern": "w",
    },
    {
        "node_id": "spm_smooth",
        "description": "Smoothing",
        "runner": "backend.app.tools.spm_smooth_runner",
        "func": "run_spm_smooth_subject",
        "output_bold_pattern": "s",
    },
]


def build_chain_validation_plan(
    subject_id: str = "sub-001",
    input_bold: str | None = None,
    input_t1w: str | None = None,
    derivatives_dir: str = "./derivatives",
    work_dir: str = "./work",
    log_dir: str = "./logs",
    matlab_command: str = "matlab",
    spm_dir: str = "./third_party/spm12",
    approved: bool = False,
    mode: str = "dry_run",
) -> dict[str, Any]:
    """Build a chain validation plan. mode: dry_run | synthetic_execute."""
    if mode not in ("dry_run", "synthetic_execute"):
        return {"ok": False, "errors": [f"Invalid mode: {mode}. Use dry_run or synthetic_execute."]}

    if mode == "synthetic_execute" and not approved:
        return {"ok": False, "errors": ["approved=true required for synthetic_execute mode."]}

    nodes = []
    for cn in CHAIN_NODES:
        nodes.append({
            "node_id": cn["node_id"],
            "description": cn["description"],
            "mode": mode,
            "depends_on_output": cn["output_bold_pattern"],
        })

    return {
        "ok": True,
        "subject_id": subject_id,
        "mode": mode,
        "approved": approved,
        "input_bold": input_bold,
        "input_t1w": input_t1w,
        "chain_nodes": nodes,
        "derivatives_dir": derivatives_dir,
        "work_dir": work_dir,
        "log_dir": log_dir,
    }


def validate_spm_chain(
    subject_id: str = "sub-001",
    input_bold: str | None = None,
    input_t1w: str | None = None,
    derivatives_dir: str = "./derivatives",
    work_dir: str = "./work",
    log_dir: str = "./logs",
    report_dir: str = "./reports/spm_chain_validation",
    matlab_command: str = "matlab",
    spm_dir: str = "./third_party/spm12",
    approved: bool = False,
    mode: str = "dry_run",
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    """Run the SPM preprocessing chain validation."""
    plan = build_chain_validation_plan(
        subject_id=subject_id,
        input_bold=input_bold,
        input_t1w=input_t1w,
        derivatives_dir=derivatives_dir,
        work_dir=work_dir,
        log_dir=log_dir,
        matlab_command=matlab_command,
        spm_dir=spm_dir,
        approved=approved,
        mode=mode,
    )

    if not plan["ok"]:
        return plan

    results = []
    all_ok = True
    errors = []

    for cn in CHAIN_NODES:
        node_result = _run_chain_node(
            cn=cn,
            subject_id=subject_id,
            input_bold=input_bold,
            input_t1w=input_t1w,
            derivatives_dir=derivatives_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            matlab_command=matlab_command,
            spm_dir=spm_dir,
            approved=approved,
            mode=mode,
        )
        results.append(node_result)

        if not node_result.get("ok"):
            all_ok = False
            errors.append(f"{cn['node_id']}: {node_result.get('errors', ['unknown'])}")
            if stop_on_failure:
                break

    summary = {
        "ok": all_ok,
        "subject_id": subject_id,
        "mode": mode,
        "approved": approved,
        "nodes_total": len(CHAIN_NODES),
        "nodes_completed": len(results),
        "nodes_passed": sum(1 for r in results if r.get("ok")),
        "nodes_failed": sum(1 for r in results if not r.get("ok")),
        "results": results,
        "errors": errors,
    }

    # Write reports
    report_out = Path(report_dir)
    report_out.mkdir(parents=True, exist_ok=True)
    (report_out / "spm_chain_validation_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# SPM Chain Validation Report",
        "",
        f"- Subject: {subject_id}",
        f"- Mode: {mode}",
        f"- Approved: {approved}",
        f"- Result: {'PASS' if all_ok else 'FAIL'}",
        "",
        "## Node Results",
        "",
        "| Node | Status | Errors |",
        "|------|--------|--------|",
    ]
    for r in results:
        status = "PASS" if r.get("ok") else "FAIL"
        errs = "; ".join(r.get("errors", [])) or "-"
        lines.append(f"| {r['node_id']} | {status} | {errs} |")

    lines += [
        "",
        "## Safety Note",
        "",
        "Chain validation uses synthetic data only. No rawdata is modified.",
    ]
    (report_out / "spm_chain_validation_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    return summary


def _run_chain_node(
    cn: dict,
    subject_id: str,
    input_bold: str | None,
    input_t1w: str | None,
    derivatives_dir: str,
    work_dir: str,
    log_dir: str,
    matlab_command: str,
    spm_dir: str,
    approved: bool,
    mode: str,
) -> dict[str, Any]:
    """Run a single chain node. In dry_run mode, generates MATLAB script only."""
    node_out = Path(work_dir) / "spm_validation" / cn["node_id"]
    node_out.mkdir(parents=True, exist_ok=True)

    if mode == "dry_run":
        matlab_script = _generate_dry_run_script(
            node_id=cn["node_id"],
            subject_id=subject_id,
            matlab_command=matlab_command,
            spm_dir=spm_dir,
            work_dir=work_dir,
            log_dir=log_dir,
            derivatives_dir=derivatives_dir,
            input_bold=input_bold,
            input_t1w=input_t1w,
        )
        script_path = node_out / "matlabbatch.m"
        script_path.write_text(matlab_script, encoding="utf-8")
        return {
            "ok": True,
            "node_id": cn["node_id"],
            "mode": "dry_run",
            "script_path": str(script_path),
            "outputs": [str(script_path)],
            "warnings": [],
            "errors": [],
        }

    # synthetic_execute mode
    if not approved:
        return {"ok": False, "node_id": cn["node_id"], "mode": mode, "errors": ["approved=true required"]}

    # Import and run the actual runner via subprocess
    import subprocess

    run_dir = Path(work_dir) / "chain_run" / cn["node_id"]
    run_dir.mkdir(parents=True, exist_ok=True)

    node_input_bold = input_bold or f"examples/synthetic_bids/rawdata/{subject_id}/func/{subject_id}_task-rest_bold.nii.gz"

    # Build Python command to run the SPM node
    script_content = f'''from src.backend.app.tools.{cn["runner"].split(".")[-1]} import {cn["func"]}
result = {cn["func"]}(
    matlab_command='{matlab_command}',
    spm_dir='{spm_dir}',
    subject_id='{subject_id}',
    input_bold='{node_input_bold}',
    derivatives_dir='{derivatives_dir}',
    work_dir='{work_dir}',
    log_dir='{log_dir}',
    approved=True,
    matlab_script_dir='./matlab',
)
import json
print(json.dumps(result, ensure_ascii=False, indent=2))
'''
    script_path = run_dir / "run_node.py"
    script_path.write_text(script_content)

    result_path = run_dir / "result.json"
    try:
        completed = reject_unreviewed_process_start(
            ["python", "-c", script_content],
            capture_output=True, text=True, timeout=300, cwd=".",
        )
        if result_path.exists():
            with open(result_path) as f:
                data = json.load(f)
            data["stdout_log"] = str(run_dir / "stdout.log")
            data["stderr_log"] = str(run_dir / "stderr.log")
        else:
            data = {"ok": False, "node_id": cn["node_id"], "errors": [completed.stderr[:500]]}
    except subprocess.TimeoutExpired:
        data = {"ok": False, "node_id": cn["node_id"], "errors": ["Node timed out (300s)"]}
    except Exception as exc:
        data = {"ok": False, "node_id": cn["node_id"], "errors": [str(exc)]}

    data["node_id"] = cn["node_id"]
    data["mode"] = mode

    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


def _generate_dry_run_script(
    node_id: str,
    subject_id: str,
    matlab_command: str,
    spm_dir: str,
    work_dir: str,
    log_dir: str,
    derivatives_dir: str,
    input_bold: str | None,
    input_t1w: str | None,
) -> str:
    """Generate a MATLAB script for dry-run mode."""
    bold = input_bold or f"examples/synthetic_bids/rawdata/{subject_id}/func/{subject_id}_task-rest_bold.nii.gz"
    t1w = input_t1w or f"examples/synthetic_bids/rawdata/{subject_id}/anat/{subject_id}_T1w.nii.gz"

    return f"""%% SPM Chain Validation - {node_id}
%% Subject: {subject_id}
%% Mode: dry_run
%% Generated: MedImage Agent v0.3.0-alpha

addpath('{spm_dir}');
spm_jobman('initcfg');

%% Node: {node_id}
%% Input BOLD: {bold}
%% Input T1w: {t1w}
%% Derivatives: {derivatives_dir}

%% This is a dry-run script. No actual SPM processing is performed.
%% Set approved=true and mode=synthetic_execute to execute.
disp('Dry run complete: {node_id}');
"""
