from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _run_command(cmd: list[str], cwd: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    completed = reject_unreviewed_process_start(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    duration = time.perf_counter() - started

    return {
        "cmd": cmd,
        "returncode": completed.returncode,
        "ok": completed.returncode == 0,
        "duration_seconds": duration,
        "stdout": completed.stdout[-8000:],
        "stderr": completed.stderr[-8000:],
    }


def run_validation_suite(
    include_api: bool = True,
    include_frontend: bool = False,
    include_gpu_optional: bool = True,
) -> dict[str, Any]:
    out_dir = Path("outputs/reports") / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "name": "pytest_unit",
            **_run_command(["python", "-m", "pytest", "tests/unit", "-q"]),
        }
    )

    checks.append(
        {
            "name": "pytest_integration",
            **_run_command(["python", "-m", "pytest", "tests/integration", "-q"]),
        }
    )

    if include_api:
        checks.append(
            {
                "name": "pytest_api",
                **_run_command(["python", "-m", "pytest", "tests/api", "-q"]),
            }
        )

    if include_gpu_optional:
        checks.append(
            {
                "name": "gpu_check_optional",
                **_run_command(["python", "-m", "backend.app.tools.gpu_benchmark_cli"]),
            }
        )

    if include_frontend:
        checks.append(
            {
                "name": "frontend_build",
                **_run_command(["npm", "run", "build"], cwd="frontend"),
            }
        )

    ok = all(item["ok"] for item in checks)

    summary = {
        "ok": ok,
        "checks_total": len(checks),
        "checks_passed": sum(1 for item in checks if item["ok"]),
        "checks_failed": sum(1 for item in checks if not item["ok"]),
        "checks": checks,
    }

    summary_path = out_dir / "validation_summary.json"
    report_path = out_dir / "validation_report.md"

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = []
    lines.append("# Validation Report")
    lines.append("")
    lines.append(f"- Overall status: {'PASS' if ok else 'FAIL'}")
    lines.append(f"- Checks total: {summary['checks_total']}")
    lines.append(f"- Checks passed: {summary['checks_passed']}")
    lines.append(f"- Checks failed: {summary['checks_failed']}")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| Check | OK | Return Code | Duration seconds |")
    lines.append("|---|---:|---:|---:|")

    for item in checks:
        lines.append(
            f"| {item['name']} | {item['ok']} | {item['returncode']} | "
            f"{item['duration_seconds']:.3f} |"
        )

    lines.append("")
    lines.append("## Failed Check Details")
    lines.append("")

    failed = [item for item in checks if not item["ok"]]
    if not failed:
        lines.append("No failed checks.")
    else:
        for item in failed:
            lines.append(f"### {item['name']}")
            lines.append("")
            lines.append("STDOUT:")
            lines.append("```text")
            lines.append(item.get("stdout", ""))
            lines.append("```")
            lines.append("")
            lines.append("STDERR:")
            lines.append("```text")
            lines.append(item.get("stderr", ""))
            lines.append("```")
            lines.append("")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    summary["outputs"] = [str(summary_path), str(report_path)]
    return summary
