from __future__ import annotations

import json
import sys
import uuid


def main() -> int:
    try:
        import requests
    except ImportError:
        print("Missing dependency: requests. Install with: pip install requests")
        return 1

    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    project_id = sys.argv[2] if len(sys.argv) > 2 else ""

    checks = []

    def call(method: str, path: str, **kwargs):
        url = base_url.rstrip("/") + path
        response = requests.request(method, url, timeout=30, **kwargs)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text}

        checks.append({
            "method": method,
            "path": path,
            "status_code": response.status_code,
            "ok": 200 <= response.status_code < 300,
            "payload": payload,
        })
        return payload

    call("GET", "/health")
    call("GET", "/api/rsfmri/preprocessing-plan")
    call("GET", "/api/rsfmri/spm-slice-timing")
    call("GET", "/api/rsfmri/spm-realign-motion-qc")
    call("GET", "/api/rsfmri/st-realign-motion-qc")
    call("GET", "/api/rsfmri/coregistration-qc")
    call("GET", "/api/rsfmri/segmentation-tissue-qc")
    call("GET", "/api/rsfmri/normalization-qc")
    call("GET", "/api/rsfmri/smoothing-qc")
    call("GET", "/api/rsfmri/nuisance-regression")
    call("GET", "/api/rsfmri/temporal-filtering")
    call("GET", "/api/rsfmri/alff-falff")
    call("GET", "/api/rsfmri/reho")
    call("GET", "/api/rsfmri/functional-connectivity")
    call("GET", "/api/rsfmri/group-summary")
    call("GET", "/api/rsfmri/report-exports/latest")
    call("GET", "/api/rsfmri/report-exports")
    call("GET", "/api/rsfmri/report-validations/latest")
    call("GET", "/api/rsfmri/report-validations")
    call("GET", "/api/release-readiness")
    call("GET", "/api/pipelines")
    if not project_id:
        print("Missing project ID: pass it as the second argument for Agent Task smoke coverage.")
        return 1

    created = call(
        "POST",
        f"/api/projects/{project_id}/agent/tasks",
        json={
            "goal": "仅生成静息态预处理方案，不执行计算。",
            "command_id": f"api-smoke-plan-only-{uuid.uuid4()}",
            "actor": "api-smoke-test",
        },
    )
    task_id = str(created.get("task_id") or "") if isinstance(created, dict) else ""
    if not task_id:
        checks.append({
            "method": "GET",
            "path": "/api/projects/{project_id}/agent/tasks/{task_id}",
            "status_code": 0,
            "ok": False,
            "payload": {"error": "Agent Task creation did not return task_id."},
        })
    else:
        call("GET", f"/api/projects/{project_id}/agent/tasks/{task_id}")
        call("GET", f"/api/projects/{project_id}/agent/tasks")

    print(json.dumps({
        "ok": all(item["ok"] for item in checks),
        "checks": checks,
    }, ensure_ascii=False, indent=2))

    return 0 if all(item["ok"] for item in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
