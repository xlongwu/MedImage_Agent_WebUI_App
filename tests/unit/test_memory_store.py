from __future__ import annotations

import json
from pathlib import Path

from src.backend.app.runtime.memory_store import (
    append_run_history,
    ensure_memory_layout,
    match_error_patterns,
)


def test_memory_layout_and_run_history(tmp_path: Path):
    ensure_memory_layout(str(tmp_path))

    history_path = append_run_history(
        project_name="test_project",
        record={"agent_run_id": "agent_test", "phi": "should_not_store"},
        root_dir=str(tmp_path),
    )

    assert history_path.exists()
    line = history_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert payload["agent_run_id"] == "agent_test"
    assert "phi" not in payload


_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_match_error_patterns_reads_category_patterns_from_error_kb():
    matches = match_error_patterns(
        ["stdout: matlab: command not found\nexit code 9009"],
        root_dir=str(_REPO_ROOT),
    )

    categories = {match["category"] for match in matches}
    assert "matlab_missing" in categories
    matlab = next(match for match in matches if match["category"] == "matlab_missing")
    assert matlab["severity"] == "critical"
    assert matlab["retryable"] is False
    assert matlab["suggested_fixes"]


def test_match_error_patterns_returns_empty_for_unknown_error():
    assert match_error_patterns(["totally unrelated gibberish"], root_dir=str(_REPO_ROOT)) == []
