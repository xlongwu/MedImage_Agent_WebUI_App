from __future__ import annotations

import json
import os

import pytest

from src.backend.app.desktop_backend_entry import run_sandbox_self_test


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows restricted-token test")


@pytest.mark.parametrize(
    "case_id,expected_code",
    (
        ("write_allowed_output", "SUCCEEDED"),
        ("write_rawdata_denied", "SUCCEEDED"),
        ("write_outside_project_denied", "SUCCEEDED"),
        ("memory_limit", "FAILED"),
        ("print_environment_keys", "SUCCEEDED"),
    ),
)
def test_fixed_windows_sandbox_cases(
    case_id: str,
    expected_code: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert run_sandbox_self_test(case_id) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["code"] == expected_code
    assert payload["network_isolation"] == "not_enforced"
    assert not list(tmp_path.glob(".sandbox-self-test-*"))
