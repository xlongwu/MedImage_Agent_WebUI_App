"""Regression tests for the Error KB API surface (GET /api/kb/errors)."""
from __future__ import annotations

from src.backend.app.api.advisor_routes import api_kb_errors
from src.backend.app.tools.error_kb_validator import list_error_kb_entries


def test_list_error_kb_entries_returns_all_tracked_categories() -> None:
    result = list_error_kb_entries()

    assert result["version"] == "0.2.0"
    assert result["categories_count"] > 0
    assert result["categories_count"] == len(result["entries"])

    matlab = next(entry for entry in result["entries"] if entry["category"] == "matlab_missing")
    assert matlab["severity"] == "critical"
    assert matlab["retryable"] is False
    assert matlab["human_action_required"] is True
    assert "MATLAB check failed" in matlab["patterns"]
    assert matlab["suggested_fixes"]
    assert "matlab" in matlab["affected_backends"]


def test_kb_errors_route_handler_executes_without_import_error() -> None:
    result = api_kb_errors()

    assert result["entries"]
    assert {entry["category"] for entry in result["entries"]} >= {"matlab_missing", "spm_path_error"}
