from __future__ import annotations

import json
import os

import pytest

from src.backend.app.desktop_backend_entry import run_sandbox_self_test


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows restricted-token test")


@pytest.mark.parametrize(
    "case_id,expected_code",
    (("spawn_child_tree", "SUCCEEDED"), ("timeout", "TIMED_OUT")),
)
def test_job_object_blocks_or_terminates_the_process_tree(
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
    assert not list(tmp_path.glob(".sandbox-self-test-*"))
