from __future__ import annotations

from pathlib import Path

from src.backend.app.runtime import desktop_config


def test_desktop_config_saves_redacted_llm_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")

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
    assert result["config"]["llm"]["api_key_set"] is True
    assert "api_key" not in result["config"]["llm"]


def test_desktop_health_reports_required_checks(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", tmp_path / "desktop_config.json")
    desktop_config.save_desktop_config({"project_dir": ".", "python_path": "."})

    result = desktop_config.get_desktop_health()

    assert result["ok"] is True
    assert any(item["name"] == "project_dir" for item in result["checks"])
    assert all(item["name"] != "gui_agent_provider" for item in result["checks"])
