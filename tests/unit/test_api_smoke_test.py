from __future__ import annotations

import sys
from types import SimpleNamespace

from src.backend.app.tools import api_smoke_test


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self) -> dict[str, object]:
        return self._payload


def test_api_smoke_uses_agent_task_plan_only_without_approval(monkeypatch) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def request(method: str, url: str, timeout: int, **kwargs):
        calls.append((method, url, kwargs))
        payload: dict[str, object] = {"ok": True}
        if url.endswith("/agent/tasks") and method == "POST":
            payload["task_id"] = "task-smoke"
        return _Response(payload)

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(request=request))
    monkeypatch.setattr(
        sys,
        "argv",
        ["api_smoke_test.py", "http://api.example", "project-smoke"],
    )

    assert api_smoke_test.main() == 0

    paths = [url.removeprefix("http://api.example") for _, url, _ in calls]
    assert "/api/projects/project-smoke/agent/tasks" in paths
    assert "/api/projects/project-smoke/agent/tasks/task-smoke" in paths
    assert "/api/agent/plan" not in paths
    assert "/api/agent-runs/agent_run_001" not in paths
    assert not any(path.endswith("/approve") for path in paths)
    create_call = next(call for call in calls if call[1].endswith("/agent/tasks") and call[0] == "POST")
    assert "\u4e0d\u6267\u884c\u8ba1\u7b97" in create_call[2]["json"]["goal"]
