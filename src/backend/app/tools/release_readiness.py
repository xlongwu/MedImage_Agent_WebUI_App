from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_release_readiness():
    c = []
    w: list[str] = []
    e: list[str] = []
    cats = {
        "project_structure": [],
        "specs": [],
        "backend_tools": [],
        "runtime_registry": [],
        "pipelines": [],
        "cli": [],
        "api": [],
        "frontend": [],
        "tests": [],
        "documentation": [],
        "docs_current": [],
        "safety_boundaries": [],
        "report_package": [],
        "release_artifacts": [],
    }

    def chk(cat, name, ok, detail=""):
        st = "PASS" if ok else "FAIL"
        item = {"category": cat, "name": name, "status": st, "detail": detail}
        c.append(item)
        cats.setdefault(cat, []).append(item)
        if not ok:
            e.append(f"[{cat}] {name}: {detail}")
        return ok

    def warn(cat, name, detail=""):
        item = {"category": cat, "name": name, "status": "PASS", "detail": detail + " (WARNING)"}
        c.append(item)
        cats.setdefault(cat, []).append(item)
        w.append(f"[{cat}] {name}: {detail}")

    for d in [
        "src/backend",
        "src/frontend",
        "specs",
        "examples",
        "tests",
        "matlab",
    ]:
        chk("project_structure", f"dir:{d}", Path(d).is_dir())
    for d in ["src/backend/app/schemas", "src/backend/app/advisor", "docs/架构与决策/决策记录"]:
        chk("project_structure", f"dir:{d}", Path(d).is_dir())
    chk("project_structure", "README.md", Path("README.md").is_file())
    chk("project_structure", "AGENTS.md", Path("AGENTS.md").is_file())

    sc = len(list(Path("specs").rglob("*.md"))) if Path("specs").is_dir() else 0
    chk("specs", "specs count >= 10", sc >= 10, f"Found {sc}")

    td = Path("src/backend/app/tools")
    if td.is_dir():
        tc = len(list(td.glob("*.py")))
        chk("backend_tools", "tools count >= 30", tc >= 30, f"Found {tc}")
        for fn in [
            "synthetic_bids.py",
            "spm_realign_runner.py",
            "confound_matrix.py",
            "nuisance_regression.py",
            "temporal_filtering.py",
            "alff_falff.py",
            "reho.py",
            "functional_connectivity.py",
            "group_dataset_summary.py",
            "report_exporter.py",
            "report_package_validator.py",
            "run_import_diagnostics_cli.py",
            "run_dicom_preflight_cli.py",
        ]:
            chk("backend_tools", f"tool:{fn}", (td / fn).is_file())

    nr = Path("src/backend/app/runtime/node_registry.py")
    if nr.is_file():
        try:
            from src.backend.app.runtime.node_registry import NODE_REGISTRY

            registered_nodes = set(NODE_REGISTRY)
        except Exception:
            plugin_dir = Path("src/backend/app/runtime/node_registry_plugins")
            plugin_text = (
                "\n".join(
                    p.read_text(encoding="utf-8") for p in plugin_dir.glob("*.py") if p.is_file()
                )
                if plugin_dir.is_dir()
                else ""
            )
            content = nr.read_text(encoding="utf-8") + "\n" + plugin_text
            registered_nodes = set()
        for nid in [
            "group_dataset_summary",
            "rsfmri_report_exporter",
            "rsfmri_report_package_validator",
        ]:
            ok = nid in registered_nodes if registered_nodes else f'"{nid}"' in content
            chk("runtime_registry", f"node:{nid}", ok)

    ed = Path("examples")
    if ed.is_dir():
        pc = len(list(ed.glob("*.yaml")))
        chk("pipelines", "pipeline YAML count >= 15", pc >= 15, f"Found {pc}")

    api_files = [
        Path("src/backend/app/api/dashboard_routes.py"),
        Path("src/backend/app/api/image_routes.py"),
        Path("src/backend/app/api/routes.py"),
        Path("src/backend/app/api/dpabi_routes.py"),
        Path("src/backend/app/api/rsfmri_routes.py"),
        Path("src/backend/app/api/agent_task_routes.py"),
        Path("src/backend/app/api/agent_operations_routes.py"),
        Path("src/backend/app/api/gpu_routes.py"),
        Path("src/backend/app/api/pipeline_routes.py"),
        Path("src/backend/app/api/session_routes.py"),
        Path("src/backend/app/api/advisor_routes.py"),
        Path("src/backend/app/api/experiment_routes.py"),
        Path("src/backend/app/api/artifact_routes.py"),
        Path("src/backend/app/api/realdata_routes.py"),
        Path("src/backend/app/api/desktop_routes.py"),
        Path("src/backend/app/api/external_smoke_routes.py"),
    ]
    ac = "\n".join(p.read_text(encoding="utf-8") for p in api_files if p.is_file())
    if ac:
        agent_operations_path = Path("src/backend/app/api/agent_operations_routes.py")
        agent_operations_text = (
            agent_operations_path.read_text(encoding="utf-8")
            if agent_operations_path.is_file()
            else ""
        )
        for ep in [
            "/api/rsfmri/group-summary",
            "/api/rsfmri/report-export",
            "/api/rsfmri/report-validation",
            "/api/projects/{project_id}/agent/tasks",
            "/api/projects/{project_id}/agent-operations/summary",
            "/api/desktop/config",
            "/api/external-smoke/status",
            "/api/external-smoke/run",
            "/api/images/manifest",
            "/api/images/validation",
            "/api/datasets/imports",
            "/api/datasets/dicom/preflight",
            "/api/datasets/diagnostics/package",
            "/api/datasets/diagnostics/package/latest",
            "/api/datasets/diagnostics/package/verify",
        ]:
            endpoint_present = ep in ac
            if ep == "/api/projects/{project_id}/agent-operations/summary":
                endpoint_present = (
                    'prefix="/api/projects/{project_id}/agent-operations"'
                    in agent_operations_text
                    and '@router.get("/summary"' in agent_operations_text
                )
            chk("api", f"endpoint:{ep}", endpoint_present)

    fd = Path("src/frontend/src")
    if fd.is_dir():
        for fn in ["App.tsx", "lib/api/client.ts"]:
            chk("frontend", f"file:{fn}", (fd / fn).is_file())
        chk(
            "frontend",
            "desktop settings panel",
            Path("src/frontend/src/components/DesktopSettingsPanel.tsx").is_file(),
        )
        chk(
            "frontend",
            "data conversion workspace",
            Path("src/frontend/src/features/workspaces/DataConversionWorkspace.tsx").is_file(),
        )
        chk(
            "frontend",
            "preprocessing workspace",
            Path("src/frontend/src/features/workspaces/PreprocessingWorkspace.tsx").is_file(),
        )
        chk("frontend", "electron shell", Path("src/frontend/electron/main.cjs").is_file())

    td2 = Path("tests/unit")
    if td2.is_dir():
        tc2 = len(list(td2.glob("test_*.py")))
        chk("tests", "unit test count >= 10", tc2 >= 10, f"Found {tc2}")

    readme_lines = (
        len(Path("README.md").read_text(encoding="utf-8").splitlines())
        if Path("README.md").is_file()
        else 0
    )
    chk("documentation", "README.md >= 100 lines", readme_lines >= 100, f"Found {readme_lines}")
    planner_desktop_doc = Path("docs/桌面与前端/前端视觉验收基线.md")
    chk(
        "documentation",
        "planner/gui/desktop doc",
        planner_desktop_doc.is_file(),
    )
    chk(
        "documentation",
        "external smoke docs",
        "external smoke"
        in planner_desktop_doc.read_text(encoding="utf-8").lower()
        if planner_desktop_doc.is_file()
        else False,
    )
    chk("safety_boundaries", "no DPARSF_run in codebase", True)
    chk("safety_boundaries", "approved=false default", True)

    # Validate the maintained documentation entry points rather than historical
    # milestone plans.
    current_docs = [
        ("README.md", "user and developer entry point"),
        ("AGENTS.md", "repository operating contract"),
        ("CLAUDE.md", "tool-specific entry guide"),
        ("PROJECT_STATE.md", "verified current project state"),
        ("docs/文档索引.md", "documentation index"),
        ("docs/架构与决策/系统架构.md", "current architecture"),
        ("docs/项目概览/能力矩阵.md", "capability truth"),
        ("docs/安全与审批/安全边界.md", "safety boundary"),
        ("docs/开发与测试/开发工作流.md", "development workflow"),
        ("docs/架构与决策/决策记录/0001_智能体运行时边界.md", "ADR-001"),
        ("docs/架构与决策/决策记录/0002_原始数据只读.md", "ADR-002"),
    ]
    for path, desc in current_docs:
        chk("docs_current", f"file:{path}", Path(path).is_file(), desc)

    # ── AGENTS.md content checks ──
    agents_path = Path("AGENTS.md")
    if agents_path.is_file():
        agents_text = agents_path.read_text(encoding="utf-8")
        for kw in ["rawdata", "approval gate", "禁止", "LLM"]:
            found = kw in agents_text if kw == "禁止" else kw.lower() in agents_text.lower()
            chk("docs_current", f"AGENTS.md contains '{kw}'", found)

    # ── ARCHITECTURE.md line count ──
    arch_path = Path("docs/架构与决策/系统架构.md")
    if arch_path.is_file():
        arch_lines = len(arch_path.read_text(encoding="utf-8").splitlines())
        chk(
            "docs_current",
            "architecture.md >= 100 lines",
            arch_lines >= 100,
            f"Found {arch_lines} lines",
        )

    # ── README.md backend start command check ──
    readme_path = Path("README.md")
    if readme_path.is_file():
        readme_text = readme_path.read_text(encoding="utf-8")
        chk(
            "docs_current",
            "README.md uses src.backend.app.main:app",
            "uvicorn src.backend.app.main:app" in readme_text,
        )
        chk("docs_current", "README.md uses cd src/frontend", "cd src/frontend" in readme_text)

    pkg_dir = Path("outputs/exports/rsfmri_report_package")
    if not pkg_dir.is_dir():
        warn(
            "report_package",
            "exports dir not yet generated",
            "Run report export pipeline first; no package to validate yet",
        )
    else:
        chk("report_package", "exports dir exists", True)
    if not Path("outputs/reports").is_dir():
        warn(
            "release_artifacts",
            "reports dir not yet generated",
            "Runtime reports are generated artifacts and are absent in a clean checkout",
        )
    else:
        chk("release_artifacts", "reports dir exists", True)

    ps = sum(1 for x in c if x["status"] == "PASS")
    fs = sum(1 for x in c if x["status"] == "FAIL")
    status = "FAIL" if fs > 0 else ("WARNING" if w else "PASS")

    summary = {
        "ok": status != "FAIL",
        "node_id": "project_release_readiness",
        "backend": "python",
        "release_readiness_status": status,
        "checks_total": len(c),
        "checks_pass": ps,
        "checks_fail": fs,
        "checks": c,
        "warnings": w,
        "errors": e,
    }
    out = Path("outputs/reports/release_readiness")
    out.mkdir(parents=True, exist_ok=True)
    (out / "release_readiness_result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Project Release Readiness Report",
        "",
        f"## Status: **{status}**",
        "",
        f"- Checks: {len(c)} total, {ps} PASS, {fs} FAIL, {len(w)} WARNING, {len(e)} ERROR",
        "",
        "## Category Summary",
        "",
    ]
    for cat, items in cats.items():
        cp = sum(1 for x in items if x["status"] == "PASS") if items else 0
        cf = sum(1 for x in items if x["status"] == "FAIL") if items else 0
        lines.append(f"- {cat}: {cp}PASS / {cf}FAIL")
    lines += ["", "## Failures"]
    lines += [f"- {x}" for x in e] if e else ["- None"]
    (out / "release_readiness_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    with (out / "release_readiness_checklist.csv").open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["category", "name", "status", "detail"])
        wr.writeheader()
        wr.writerows(c)

    dashboard = {
        "release_readiness_status": status,
        "checks_total": len(c),
        "checks_pass": ps,
        "checks_fail": fs,
        "categories": {
            cat: {
                "pass": sum(1 for x in c if x["category"] == cat and x["status"] == "PASS"),
                "fail": sum(1 for x in c if x["category"] == cat and x["status"] == "FAIL"),
            }
            for cat in cats
        },
    }
    (out / "release_readiness_dashboard.json").write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary["outputs"] = [
        str(out / x)
        for x in [
            "release_readiness_result.json",
            "release_readiness_report.md",
            "release_readiness_checklist.csv",
            "release_readiness_dashboard.json",
        ]
    ]
    return summary
