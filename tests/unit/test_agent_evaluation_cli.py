from __future__ import annotations

import json
import sys

from scripts.run_agent_evaluation import main


def test_live_provider_requires_explicit_network_opt_in(
    monkeypatch, tmp_path, capsys
) -> None:
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "run_agent_evaluation.py",
        "--manifest",
        str(tmp_path / "unused.json"),
        "--provider",
        "openai_compatible",
        "--output",
        str(output),
    ])

    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "error_code": "AGENT_EVAL_NETWORK_NOT_ALLOWED"
    }
    assert not output.exists()


def test_invalid_manifest_fails_without_partial_report(
    monkeypatch, tmp_path, capsys
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "run_agent_evaluation.py",
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ])

    assert main() == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "ValidationError"
    assert not output.exists()
