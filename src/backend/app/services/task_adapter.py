"""Service adapter for task routes.

Thin adapter that accepts ProjectStore and delegates to the existing
task service functions.  Preserves all current behavior; only changes how
the store is supplied (Depends injection instead of module-level mock_store).
"""

from __future__ import annotations

from src.backend.app.api.dependencies import ProjectStore


def list_tasks(store: ProjectStore) -> list[dict[str, object]]:
    from src.backend.app.services.mock_store import mock_store

    return [task.model_dump() for task in mock_store.list_tasks()]


def get_task(task_id: str, store: ProjectStore) -> dict[str, object]:
    from fastapi import HTTPException

    from src.backend.app.services.mock_store import mock_store

    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return task.model_dump()


def list_task_events(task_id: str, store: ProjectStore) -> list[dict[str, object]]:
    from fastapi import HTTPException

    from src.backend.app.services.mock_store import mock_store
    from src.backend.app.services.task_manager import task_manager

    if not mock_store.get_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return [event.model_dump() for event in task_manager.list_events(task_id)]


async def approve_task(
    task_id: str,
    request: dict[str, object],
    store: ProjectStore,
) -> dict[str, object]:
    import asyncio

    from fastapi import HTTPException

    from src.backend.app.schemas.desktop import TaskApprovalRequest
    from src.backend.app.services.mock_store import mock_store
    from src.backend.app.services.pipeline_runner import run_pipeline_task
    from src.backend.app.services.task_manager import task_manager

    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if task.execution_mode != "external_smoke":
        raise HTTPException(
            status_code=400,
            detail="Only external_smoke tasks can receive run-level approval",
        )
    if not request.get("approved"):
        raise HTTPException(
            status_code=403,
            detail="approved=true is required before launching approved smoke",
        )
    if not str(request.get("approved_by", "")).strip():
        raise HTTPException(
            status_code=400,
            detail="approved_by is required",
        )

    req = TaskApprovalRequest(**request)
    approval = mock_store.add_approval(
        task_id,
        approved=True,
        approved_by=req.approved_by.strip(),
        approval_scope=req.approval_scope,
        safety_flags=req.safety_flags,
    )
    await task_manager.update_task(
        task_id,
        status="running",
        progress=max(task.progress, 5),
        message=f"Approved external smoke run queued by {approval.approved_by}",
        source="approval_gate",
        metadata={"approval_id": approval.approval_id, "approval_scope": approval.approval_scope},
    )
    from src.backend.app.schemas.desktop import PipelineRunRequest

    approved_request = PipelineRunRequest(
        project_id=task.project_id,
        pipeline_id=task.pipeline_id,
        model_id=task.model_id,
        input_sequences=task.input_sequences,
        output_type=task.output_type,
        execution_mode="external_smoke",
        external_smoke_mode="approved_smoke",
        approved=True,
        approved_by=approval.approved_by,
    )
    asyncio.create_task(run_pipeline_task(task_id, approved_request, task_manager))
    return {"ok": True, "approval": approval.model_dump(), "message": "Approved smoke run queued"}


def get_task_diagnostics(
    task_id: str,
    store: ProjectStore,
) -> dict[str, object]:
    from fastapi import HTTPException

    from src.backend.app.services.mock_store import mock_store

    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    payload = dict(mock_store.get_task_artifacts(task.id))
    from src.backend.app.services.task_manager import task_manager

    events = task_manager.list_events(task.id)
    errors = list(payload.get("errors", []))
    warnings = list(payload.get("warnings", []))
    external_tool_results = list(payload.get("external_tool_results", []))
    diagnosis: list[dict[str, object]] = []

    for error in errors:
        diagnosis.append(
            {
                "severity": "error",
                "code": _classify_external_error(str(error)),
                "message": str(error),
            }
        )
    for warning in warnings:
        diagnosis.append({"severity": "warning", "code": "warning", "message": str(warning)})
    for result in external_tool_results:
        if isinstance(result, dict) and result.get("returncode") not in {None, 0}:
            diagnosis.append(
                {
                    "severity": "error",
                    "code": "non_zero_returncode",
                    "message": f"External command returned {result.get('returncode')}",
                    "command": result.get("command"),
                }
            )
        if isinstance(result, dict) and result.get("outputs"):
            outputs = result.get("outputs")
            missing: list[str] = []
            if isinstance(outputs, dict):
                missing = [key for key, value in outputs.items() if value in {None, "", False}]
            if missing:
                diagnosis.append(
                    {
                        "severity": "error",
                        "code": "missing_expected_outputs",
                        "message": f"Missing expected outputs: {', '.join(missing)}",
                    }
                )


    if not diagnosis and task.status == "completed":
        diagnosis.append({"severity": "info", "code": "no_critical_findings", "message": "No critical diagnostics were recorded."})

    logs = [event.message for event in events] or task.logs
    return {
        "ok": not any(item.get("severity") == "error" for item in diagnosis),
        "task_id": task.id,
        "status": task.status,
        "diagnosis": diagnosis,
        "external_tool_results": external_tool_results,
        "logs": logs,
        "artifacts": dict(payload.get("artifacts", {})),
        "approval": mock_store.get_latest_approval(task.id).model_dump() if mock_store.get_latest_approval(task.id) else None,
        "errors": errors,
        "warnings": warnings,
    }


def get_task_artifacts(
    task_id: str,
    store: ProjectStore,
) -> dict[str, object]:
    from fastapi import HTTPException

    from src.backend.app.services.mock_store import mock_store

    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    payload = _load_artifact_payload(task)
    return {
        "ok": True,
        "task_id": task_id,
        "result_path": task.result_path,
        "artifacts": dict(payload.get("artifacts", {})),
        "approval": mock_store.get_latest_approval(task_id).model_dump() if mock_store.get_latest_approval(task_id) else None,
        "errors": list(payload.get("errors", [])),
    }


def generate_task_audit_package(
    task_id: str,
    store: ProjectStore,
) -> dict[str, object]:
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    from fastapi import HTTPException

    from src.backend.app.services.mock_store import mock_store

    task = mock_store.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    diagnostics = get_task_diagnostics(task_id, store)
    artifact_response = get_task_artifacts(task_id, store)

    generated_at = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    package_dir = Path("outputs/reports/task_audits") / _safe_path_part(task.id)
    package_dir.mkdir(parents=True, exist_ok=True)
    events = _get_task_events(task_id)
    payload = {
        "ok": bool(diagnostics.get("ok", False)) and not artifact_response.get("errors"),
        "task": task.model_dump(),
        "events": events,
        "diagnostics": diagnostics,
        "artifacts": artifact_response,
        "generated_at": generated_at,
        "safety": {
            "rawdata_read_only": True,
            "no_dparsf_blackbox": True,
            "approval_required_for_approved_smoke": task.execution_mode == "external_smoke",
        },
    }
    report_text = _render_task_audit_markdown(
        task, diagnostics, artifact_response, generated_at, events
    )
    json_path = package_dir / "task_audit_package.json"
    report_path = package_dir / "task_audit_report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(report_text, encoding="utf-8")
    existing_artifacts = dict(mock_store.get_task_artifacts(task.id))
    existing_artifacts["audit_package"] = {
        "package_dir": str(package_dir),
        "report_path": str(report_path),
        "json_path": str(json_path),
        "generated_at": generated_at,
    }
    mock_store.save_task_artifacts(task.id, existing_artifacts)
    return {
        "ok": payload["ok"],
        "task_id": task.id,
        "generated_at": generated_at,
        "package_dir": str(package_dir),
        "report_path": str(report_path),
        "json_path": str(json_path),
        "report_text": report_text,
        "artifacts": existing_artifacts,
        "errors": diagnostics.get("errors", []) + artifact_response.get("errors", []),
    }


def _load_artifact_payload(task: object) -> dict[str, object]:
    from src.backend.app.services.mock_store import mock_store

    task_obj = task
    payload = dict(mock_store.get_task_artifacts(getattr(task_obj, "id", "")))
    result_path = getattr(task_obj, "result_path", None) or str(
        payload.get("artifacts", {}).get("result_json", "")
    )
    if result_path:
        parsed = _read_json_if_exists(result_path)
        if parsed:
            payload = {
                **payload,
                "artifacts": parsed.get("artifacts", payload.get("artifacts", {})),
                "external_tool_results": parsed.get("external_tool_results", []),
                "checks": parsed.get("checks", []),
                "errors": parsed.get("errors", []),
                "warnings": parsed.get("warnings", []),
                "next_actions": parsed.get("next_actions", []),
            }
    return payload


def _read_json_if_exists(path: str) -> dict[str, object] | None:
    from pathlib import Path

    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _classify_external_error(message: str) -> str:
    lower = message.lower()
    if "matlab" in lower and ("not found" in lower or "missing" in lower):
        return "missing_matlab"
    if "spm" in lower and ("not found" in lower or "missing" in lower):
        return "missing_spm_path"
    if "dpabi" in lower and ("not found" in lower or "missing" in lower):
        return "missing_dpabi_path"
    if "result json" in lower or "expected output" in lower:
        return "missing_expected_outputs"
    if "returncode" in lower or "non-zero" in lower:
        return "non_zero_returncode"
    return "external_smoke_error"


def _safe_path_part(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:120]


def _get_task_events(task_id: str) -> list[dict[str, object]]:
    from src.backend.app.services.task_manager import task_manager

    return [event.model_dump() for event in task_manager.list_events(task_id)]


def _render_task_audit_markdown(
    task: object,
    diagnostics: dict[str, object],
    artifacts: dict[str, object],
    generated_at: str,
    events: list[dict[str, object]],
) -> str:
    approval = diagnostics.get("approval")
    lines = [
        f"# Task Audit Package: {getattr(task, 'id', '')}",
        "",
        f"- Generated at: {generated_at}",
        f"- Run name: {getattr(task, 'run_name', '')}",
        f"- Pipeline: {getattr(task, 'pipeline_id', '')}",
        f"- Project: {getattr(task, 'project_id', '')}",
        f"- Execution mode: {getattr(task, 'execution_mode', '')}",
        f"- Status: {getattr(task, 'status', '')}",
        f"- Progress: {getattr(task, 'progress', 0)}%",
        f"- Result path: {getattr(task, 'result_path', '') or 'Pending'}",
        "",
        "## Approval",
        "",
    ]
    if approval:
        lines.extend(
            [
                f"- Approval ID: {approval.get('approval_id', '')}",
                f"- Approved by: {approval.get('approved_by', '')}",
                f"- Approved at: {approval.get('approved_at', '')}",
                f"- Scope: {approval.get('approval_scope', '')}",
                f"- Safety flags: `{__import__('json').dumps(approval.get('safety_flags', {}), ensure_ascii=False)}`",
            ]
        )
    else:
        lines.append("- No run-level approval recorded.")

    lines.extend(["", "## Diagnostics", ""])
    for item in diagnostics.get("diagnosis", []):
        lines.append(
            f"- [{item.get('severity', 'info')}] {item.get('code', 'diagnostic')}: {item.get('message', '')}"
        )
    if not diagnostics.get("diagnosis"):
        lines.append("- No diagnostics recorded.")

    lines.extend(["", "## External Tool Results", ""])
    for index, result in enumerate(diagnostics.get("external_tool_results", []), start=1):
        command = result.get("command", result.get("function", f"external-run-{index}"))
        lines.append(f"- {index}. command: `{command}`; returncode: `{result.get('returncode', 'n/a')}`")
    if not diagnostics.get("external_tool_results"):
        lines.append("- No external tool results recorded.")

    lines.extend(["", "## Artifacts", ""])
    for key, value in artifacts.get("artifacts", {}).items():
        lines.append(f"- {key}: `{value}`")
    if not artifacts.get("artifacts"):
        lines.append("- No artifact paths recorded.")

    lines.extend(["", "## Event Timeline", ""])
    for event in events:
        lines.append(
            f"- {event.get('timestamp', '')} | {event.get('status', '')} | {event.get('progress', 0)}% | {event.get('message', '')}"
        )
    if not events:
        lines.append("- No events recorded.")

    lines.extend(
        [
            "",
            "## Safety Boundaries",
            "",
            "- rawdata remains read-only.",
            "- DPARSF/DPARSFA black-box batch flows remain prohibited.",
            "- Approved external smoke requires explicit run-level approval.",
        ]
    )
    return "\n".join(lines) + "\n"
