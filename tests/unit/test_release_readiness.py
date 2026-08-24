from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.backend.app.tools.release_readiness import build_release_readiness


@pytest.fixture(scope="module")
def result():
    """Run release readiness once and reuse across tests."""
    return build_release_readiness()


@pytest.fixture(scope="module")
def checks(result):
    """Build lookup dict: (category, name) → status."""
    return {(item["category"], item["name"]): item["status"] for item in result["checks"]}


# ── Structural: result shape ──


def test_result_has_expected_keys(result):
    for key in [
        "ok",
        "release_readiness_status",
        "checks_total",
        "checks_pass",
        "checks_fail",
        "checks",
        "warnings",
        "errors",
        "outputs",
    ]:
        assert key in result, f"Missing key: {key}"


def test_checks_is_non_empty(result):
    assert len(result["checks"]) > 0


def test_clean_checkout_is_not_a_readiness_failure(result):
    assert result["release_readiness_status"] != "FAIL"


# ── Project structure uses current src/ paths, not old ones ──


def test_project_structure_uses_src_backend(checks):
    assert checks[("project_structure", "dir:src/backend")] == "PASS"


def test_project_structure_uses_src_frontend(checks):
    assert checks[("project_structure", "dir:src/frontend")] == "PASS"


def test_project_structure_uses_src_backend_schemas(checks):
    assert checks[("project_structure", "dir:src/backend/app/schemas")] == "PASS"


def test_project_structure_uses_src_backend_advisor(checks):
    assert checks[("project_structure", "dir:src/backend/app/advisor")] == "PASS"


def test_project_structure_no_old_backend_path(checks):
    """Verify we do NOT check 'backend/' (without src/ prefix)."""
    backend_keys = [k for k in checks if k[0] == "project_structure" and "backend" in k[1]]
    for key in backend_keys:
        assert not key[1].startswith("dir:backend/"), f"Old path found: {key[1]}"


def test_project_structure_no_old_frontend_path(checks):
    """Verify we do NOT check 'frontend/' (without src/ prefix)."""
    fe_keys = [k for k in checks if k[0] == "project_structure" and "frontend" in k[1]]
    for key in fe_keys:
        assert not key[1].startswith("dir:frontend/"), f"Old path found: {key[1]}"


# ── Bug fix: /api/rsfmri/report-validation (not report-validator) ──


def test_api_endpoint_report_validation_fixed(checks):
    """The old name 'report-validator' was a bug; correct endpoint is 'report-validation'."""
    assert ("api", "endpoint:/api/rsfmri/report-validation") in checks
    assert ("api", "endpoint:/api/rsfmri/report-validator") not in checks
    assert checks[("api", "endpoint:/api/rsfmri/report-validation")] == "PASS"


CURRENT_DOC_PATHS = [
    "file:README.md",
    "file:AGENTS.md",
    "file:CLAUDE.md",
    "file:PROJECT_STATE.md",
    "file:docs/文档索引.md",
    "file:docs/架构与决策/系统架构.md",
    "file:docs/项目概览/能力矩阵.md",
    "file:docs/安全与审批/安全边界.md",
    "file:docs/开发与测试/开发工作流.md",
    "file:docs/架构与决策/决策记录/0001_智能体运行时边界.md",
    "file:docs/架构与决策/决策记录/0002_原始数据只读.md",
]


def test_current_docs_all_present(checks):
    for name in CURRENT_DOC_PATHS:
        key = ("docs_current", name)
        assert key in checks, f"Missing current-doc check: {name}"
        assert checks[key] == "PASS", f"Current doc {name} is {checks[key]}"


def test_current_docs_count(checks):
    current_keys = [k for k in checks if k[0] == "docs_current" and k[1].startswith("file:")]
    assert len(current_keys) >= len(CURRENT_DOC_PATHS)


# ── AGENTS.md content checks ──


def test_agents_md_contains_rawdata(checks):
    assert checks.get(("docs_current", "AGENTS.md contains 'rawdata'")) == "PASS"


def test_agents_md_contains_approval_gate(checks):
    assert checks.get(("docs_current", "AGENTS.md contains 'approval gate'")) == "PASS"


def test_agents_md_contains_forbidden(checks):
    assert checks.get(("docs_current", "AGENTS.md contains '禁止'")) == "PASS"


def test_agents_md_contains_llm(checks):
    assert checks.get(("docs_current", "AGENTS.md contains 'LLM'")) == "PASS"


# ── ARCHITECTURE.md line count ──


def test_architecture_md_line_count(checks):
    arch_key = ("docs_current", "architecture.md >= 100 lines")
    assert arch_key in checks
    assert checks[arch_key] == "PASS"


# ── README.md uses correct start commands ──


def test_readme_uses_correct_backend_command(checks):
    assert checks[("docs_current", "README.md uses src.backend.app.main:app")] == "PASS"


def test_readme_uses_correct_frontend_command(checks):
    assert checks[("docs_current", "README.md uses cd src/frontend")] == "PASS"


# ── Existing import diagnostics surface (regression) ──


def test_import_diagnostics_api_endpoints(checks):
    endpoints = [
        "/api/images/manifest",
        "/api/images/validation",
        "/api/datasets/imports",
        "/api/datasets/dicom/preflight",
        "/api/datasets/diagnostics/package",
        "/api/datasets/diagnostics/package/latest",
        "/api/datasets/diagnostics/package/verify",
    ]
    for ep in endpoints:
        key = ("api", f"endpoint:{ep}")
        assert key in checks, f"Missing endpoint check: {ep}"
        assert checks[key] == "PASS", f"Endpoint {ep} is {checks.get(key)}"


def test_import_diagnostics_tool_present(checks):
    assert checks[("backend_tools", "tool:run_dicom_preflight_cli.py")] == "PASS"


def test_current_agent_first_workspaces_are_present(checks):
    assert checks[("frontend", "agent workspace")] == "PASS"
    assert checks[("frontend", "runs workspace")] == "PASS"
    assert checks[("frontend", "settings workspace")] == "PASS"


# ── Output files ──


def test_output_files_written(result):
    out_dir = Path("outputs/reports/release_readiness")
    for fname in [
        "release_readiness_result.json",
        "release_readiness_report.md",
        "release_readiness_checklist.csv",
        "release_readiness_dashboard.json",
    ]:
        fpath = out_dir / fname
        assert fpath.is_file(), f"Missing output: {fpath}"


def test_output_result_json_is_valid(result):
    json_path = Path("outputs/reports/release_readiness/release_readiness_result.json")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["release_readiness_status"] == result["release_readiness_status"]


# ── Report package: must not be a hard FAIL when dir missing ──


def test_report_package_missing_is_not_fail(checks):
    """When exports dir is missing, the check should still be PASS (with WARNING)."""
    pkg_keys = [k for k in checks if k[0] == "report_package"]
    for key in pkg_keys:
        assert checks[key] != "FAIL", (
            f"report_package check {key} should never be FAIL — "
            "use WARNING when package not yet generated"
        )
