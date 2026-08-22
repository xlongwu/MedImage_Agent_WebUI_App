from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Any

from src.backend.app.tools.artifact_utils import write_json_artifact
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start

REQUIRED_DEPLOYMENT_FILES = [
    ".env.example",
    "deploy/local_profile.yaml",
    "deploy/docker-compose.demo.yml",
    "deploy/backend.Dockerfile",
    "deploy/frontend.Dockerfile",
    "deploy/nginx.conf",
]

FORBIDDEN_COPY_PATTERNS = [
    "COPY third_party",
    "COPY .git",
    "COPY node_modules",
    "COPY frontend/node_modules",
    "COPY examples/synthetic_bids/rawdata",
]


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _run_cmd(cmd: list[str], timeout: int = 5) -> dict[str, Any]:
    try:
        completed = reject_unreviewed_process_start(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:5000],
            "stderr": completed.stderr[:5000],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def _check_file_exists(path_text: str) -> dict[str, Any]:
    path = Path(path_text)
    return {
        "name": path_text,
        "ok": path.exists(),
        "message": "exists" if path.exists() else "missing",
    }


def _check_forbidden_patterns(path_text: str) -> list[dict[str, Any]]:
    path = Path(path_text)
    text = _read_text(path)
    checks = []

    for pattern in FORBIDDEN_COPY_PATTERNS:
        found = pattern in text
        checks.append({
            "name": f"{path_text}:{pattern}",
            "ok": not found,
            "message": "not found" if not found else "forbidden pattern found",
        })

    return checks


def build_deployment_profile(
    work_dir: str = "./work",
    report_dir: str = "./reports",
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    blockers: list[str] = []

    for item in REQUIRED_DEPLOYMENT_FILES:
        check = _check_file_exists(item)
        checks.append(check)
        if not check["ok"]:
            blockers.append(f"Missing deployment file: {item}")

    for dockerfile in ["deploy/backend.Dockerfile", "deploy/frontend.Dockerfile"]:
        if Path(dockerfile).exists():
            checks.extend(_check_forbidden_patterns(dockerfile))

    for check in checks:
        if not check.get("ok") and ":" in check.get("name", ""):
            blockers.append(f"Forbidden deployment pattern: {check.get('name')}")

    docker_version = _run_cmd(["docker", "--version"])
    docker_compose_version = _run_cmd(["docker", "compose", "version"])
    node_version = _run_cmd(["node", "--version"])
    npm_version = _run_cmd(["npm", "--version"])

    if not docker_version.get("ok"):
        warnings.append("Docker CLI not available. Docker demo profile can still be reviewed but not run locally.")

    if not docker_compose_version.get("ok"):
        warnings.append("Docker Compose plugin not available.")

    env_example = _read_text(Path(".env.example"))
    required_env_tokens = [
        "MEDIMAGE_MATLAB_ENABLED=false",
        "MEDIMAGE_ALLOW_FULL_DPABI_EXECUTION=false",
        "MEDIMAGE_ALLOW_DPARSF_RUN=false",
        "MEDIMAGE_ALLOW_DPARSFA_RUN=false",
        "MEDIMAGE_ALLOW_RAWDATA_WRITE=false",
        "MEDIMAGE_SYNTHETIC_ONLY=true",
    ]

    for token in required_env_tokens:
        ok = token in env_example
        checks.append({
            "name": f".env.example:{token}",
            "ok": ok,
            "message": "present" if ok else "missing",
        })
        if not ok:
            blockers.append(f"Missing safety env token: {token}")

    compose_text = _read_text(Path("deploy/docker-compose.demo.yml"))
    compose_required_tokens = [
        'MEDIMAGE_MATLAB_ENABLED: "false"',
        'MEDIMAGE_ALLOW_FULL_DPABI_EXECUTION: "false"',
        'MEDIMAGE_ALLOW_DPARSF_RUN: "false"',
        'MEDIMAGE_ALLOW_DPARSFA_RUN: "false"',
        'MEDIMAGE_ALLOW_RAWDATA_WRITE: "false"',
        'MEDIMAGE_SYNTHETIC_ONLY: "true"',
    ]

    for token in compose_required_tokens:
        ok = token in compose_text
        checks.append({
            "name": f"docker-compose.demo.yml:{token}",
            "ok": ok,
            "message": "present" if ok else "missing",
        })
        if not ok:
            blockers.append(f"Missing docker safety env token: {token}")

    status = "READY" if not blockers else "BLOCKED"
    if warnings and status == "READY":
        status = "WARNING"

    out_dir = Path(work_dir) / "deployment"
    report_out = Path(report_dir) / "deployment"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_out.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "deployment_profile.json"
    report_path = report_out / "deployment_profile_report.md"

    payload = {
        "ok": status in {"READY", "WARNING"},
        "node_id": "deployment_profile",
        "backend": "python",
        "status": status,
        "checks_total": len(checks),
        "checks_passed": sum(1 for item in checks if item.get("ok")),
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "environment": {
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "docker_version": docker_version,
            "docker_compose_version": docker_compose_version,
            "node_version": node_version,
            "npm_version": npm_version,
        },
        "profiles": {
            "local_dev": {
                "backend": "uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000",
                "frontend": "cd frontend && npm run dev",
                "matlab_enabled_by_default": False,
            },
            "docker_demo": {
                "compose_file": "deploy/docker-compose.demo.yml",
                "matlab_enabled_by_default": False,
                "full_dpabi_execution_allowed": False,
                "rawdata_write_allowed": False,
            },
        },
        "safety": {
            "pipelines_executed": False,
            "docker_build_executed": False,
            "docker_compose_executed": False,
            "matlab_launched": False,
            "dpabi_executed": False,
            "dparsf_run_executed": False,
            "dpabi_gui_called": False,
            "rawdata_modified": False,
            "files_deleted": False,
            "cloud_deployment_performed": False,
        },
    }

    write_json_artifact(json_path, payload)

    lines = []
    lines.append("# Deployment Profile Report")
    lines.append("")
    lines.append(f"- Status: {status}")
    lines.append(f"- Checks passed: {payload['checks_passed']}/{payload['checks_total']}")
    lines.append(f"- Blockers: {len(blockers)}")
    lines.append(f"- Warnings: {len(warnings)}")
    lines.append("")
    lines.append("## Local Dev")
    lines.append("")
    lines.append("```bash")
    lines.append("uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000")
    lines.append("cd frontend && npm run dev")
    lines.append("```")
    lines.append("")
    lines.append("## Docker Demo")
    lines.append("")
    lines.append("```bash")
    lines.append("docker compose -f deploy/docker-compose.demo.yml up --build")
    lines.append("```")
    lines.append("")
    lines.append("## Blockers")
    lines.append("")
    if blockers:
        for b in blockers:
            lines.append(f"- ❌ {b}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Warnings")
    lines.append("")
    if warnings:
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    for c in checks:
        icon = "✅" if c.get("ok") else "❌"
        lines.append(f"- {icon} {c.get('name')}: {c.get('message')}")
    lines.append("")
    lines.append("## Safety Guarantees")
    lines.append("")
    lines.append("- Pipelines executed: False")
    lines.append("- Docker build executed: False")
    lines.append("- Docker compose executed: False")
    lines.append("- MATLAB launched: False")
    lines.append("- DPABI executed: False")
    lines.append("- DPARSF_run executed: False")
    lines.append("- DPABI GUI called: False")
    lines.append("- Rawdata modified: False")
    lines.append("- Files deleted: False")
    lines.append("- Cloud deployment performed: False")
    lines.append("")
    lines.append("---")
    lines.append("Generated by deployment_profile scanner")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    payload["outputs"] = [
        str(json_path),
        str(report_path),
    ]

    return payload
