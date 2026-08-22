from __future__ import annotations

import json
from pathlib import Path

from src.backend.app.runtime import desktop_config


def test_desktop_config_rejects_legacy_llm_settings_and_projects_agent_model(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    monkeypatch.setenv("MEDIMAGE_AGENT_MODEL_PROVIDER", "rule_based")

    result = desktop_config.save_desktop_config(
        {
            "project_dir": ".",
            "llm": {
                "enabled": True,
                "base_url": "https://example.test/v1",
                "model": "planner-model",
                "api_key": "secret-value",
            },
        }
    )

    assert result["ok"] is True
    assert "llm" not in result["config"]
    assert result["config"]["agent_model"]["provider"] == "rule_based"
    persisted = json.loads(desktop_config.DESKTOP_CONFIG_PATH.read_text(encoding="utf-8"))
    assert "llm" not in persisted
    assert "agent_model" not in persisted


def test_desktop_health_reports_required_checks(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    desktop_config.save_desktop_config({"project_dir": ".", "python_path": "."})

    result = desktop_config.get_desktop_health()

    assert result["ok"] is True
    assert any(item["name"] == "project_dir" for item in result["checks"])
    assert any(item["name"] == "agent_model_config" for item in result["checks"])
