from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.capability_enforcement import (
    enforce_node_capabilities,
    enforce_recovery_pipeline_scope,
    filter_recovery_subjects,
)
from src.backend.app.runtime.execution_gateway import (
    VerifiedExecutionContext,
    assert_verified_execution_context,
)
from src.backend.app.runtime.node_registry import NodeExecutionContext, get_node_runner
from src.backend.app.runtime.scheduler import get_scheduler_config
from src.backend.app.runtime.state_store import (
    determine_status_from_result,
    now_iso,
    write_node_state,
    write_pipeline_summary,
)
from src.backend.app.runtime.tool_execution_context import ToolExecutionContext
from src.backend.app.schemas.pipeline_schema import (
    PipelineNode,
    PipelineValidationError,
    load_pipeline_yaml,
)


def _elapsed_seconds(started_at: str, ended_at: str) -> float:
    try:
        return max(
            0.0,
            (datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)).total_seconds(),
        )
    except (TypeError, ValueError):
        return 0.0


def load_project_config(path: str | Path) -> dict[str, Any]:
    """Load and validate a project config YAML file.

    Uses ProjectSettings.from_yaml() to validate critical fields (work_dir,
    log_dir, spm_dir, dpabi_dir) before returning the raw dict.  The returned
    dict is kept for backward compatibility — run_pipeline() still accesses
    config fields via subscript notation.
    """
    # ── structural validation (M1-T003 / M1-T005b) ──
    from src.backend.app.config import ProjectSettings  # noqa: E402

    ProjectSettings.from_yaml(path)  # raises on missing critical fields

    # ── return raw dict for backward compat ──
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: PyYAML. Install with: pip install pyyaml"
        ) from exc

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Project config file not found: {path}")

    content = path.read_text(encoding="utf-8")
    return yaml.safe_load(content)


def load_dataset_index(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"subjects": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"subjects": []}


def get_complete_subjects(dataset_index: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in dataset_index.get("subjects", []) if s.get("status") == "COMPLETE"]


def run_pipeline(
    project_config_path: str | Path,
    pipeline_path: str | Path,
    *,
    execution_context: VerifiedExecutionContext | None = None,
) -> dict[str, Any]:
    assert_verified_execution_context(execution_context)
    assert execution_context is not None
    resolved_config = str(Path(project_config_path).expanduser().resolve())
    resolved_pipeline = str(Path(pipeline_path).expanduser().resolve())
    if resolved_config != execution_context.verified_project_config_path:
        raise SafetyError(
            "EXECUTION_CONTEXT_PROJECT_CONFIG_MISMATCH",
            code="EXECUTION_CONTEXT_PROJECT_CONFIG_MISMATCH",
        )
    if resolved_pipeline != execution_context.verified_pipeline_path:
        raise SafetyError(
            "EXECUTION_CONTEXT_PIPELINE_MISMATCH",
            code="EXECUTION_CONTEXT_PIPELINE_MISMATCH",
        )
    if execution_context.ticket.is_expired():
        raise SafetyError("EXECUTION_TICKET_EXPIRED", code="EXECUTION_TICKET_EXPIRED")
    started_at = now_iso()
    errors: list[str] = []
    node_states: list[str] = []
    node_results: list[dict[str, Any]] = []

    try:
        project_config = load_project_config(project_config_path)
    except Exception as exc:
        return {
            "status": "INVALID",
            "error": f"Failed to load project config: {exc}",
            "started_at": started_at,
            "ended_at": now_iso(),
        }

    try:
        pipeline = load_pipeline_yaml(pipeline_path)
    except PipelineValidationError as exc:
        ended_at = now_iso()
        summary_path = write_pipeline_summary(
            run_id="unknown",
            pipeline_id="unknown",
            status="INVALID",
            started_at=started_at,
            ended_at=ended_at,
            node_states=[],
            node_results=[],
            errors=[str(exc)],
            work_dir=project_config.get("runtime", {}).get("work_dir", "./work"),
        )
        return {
            "status": "INVALID",
            "error": str(exc),
            "summary_path": str(summary_path),
            "started_at": started_at,
            "ended_at": ended_at,
        }
    except Exception as exc:
        ended_at = now_iso()
        work_dir = project_config.get("runtime", {}).get("work_dir", "./work")
        summary_path = write_pipeline_summary(
            run_id="unknown",
            pipeline_id="unknown",
            status="INVALID",
            started_at=started_at,
            ended_at=ended_at,
            node_states=[],
            node_results=[],
            errors=[f"Failed to load pipeline: {exc}"],
            work_dir=work_dir,
        )
        return {
            "status": "INVALID",
            "error": str(exc),
            "summary_path": str(summary_path),
            "started_at": started_at,
            "ended_at": ended_at,
        }

    run_id = pipeline.execution.get("run_id", "run_default")
    enforce_recovery_pipeline_scope(
        execution_context.ticket,
        pipeline_node_ids=(node.id for node in pipeline.nodes),
        run_id=run_id,
    )
    stop_on_failure = pipeline.execution.get("stop_on_failure", True)

    work_dir = project_config["runtime"]["work_dir"]
    log_dir = project_config["runtime"]["log_dir"]
    matlab_command = project_config["runtime"]["matlab_command"]
    spm_dir = project_config["third_party"]["spm_dir"]
    dpabi_dir = project_config["third_party"]["dpabi_dir"]

    derivatives_dir = project_config["runtime"].get("derivatives_dir", "./derivatives")

    context = NodeExecutionContext(
        run_id=run_id,
        project_config=project_config,
        work_dir=work_dir,
        log_dir=log_dir,
        matlab_command=matlab_command,
        spm_dir=spm_dir,
        dpabi_dir=dpabi_dir,
        derivatives_dir=derivatives_dir,
        tool_execution_context=ToolExecutionContext.from_ticket(
            execution_context.ticket,
            execution_context.ticket_service,
        ),
    )

    node_status_map: dict[str, str] = {}
    failed_subjects: set[str] = set()

    scheduler_config = get_scheduler_config(project_config, pipeline.execution)
    scheduler_mode = scheduler_config.get("mode", "sequential")
    max_workers = scheduler_config.get("max_workers", 1)
    matlab_max_workers = scheduler_config.get("matlab_max_workers", 1)
    gpu_max_workers = scheduler_config.get("gpu_max_workers", 1)
    gpu_mode = scheduler_config.get("gpu_mode", "prefer")

    gpu_info: dict[str, Any] | None = None
    if gpu_mode != "off":
        try:
            from src.backend.app.tools.gpu_utils import detect_gpu

            gpu_info = detect_gpu()
        except ImportError:
            gpu_info = {
                "ok": True,
                "gpu_available": False,
                "warnings": ["Cannot import GPU detection."],
                "errors": [],
            }

    for node in pipeline.nodes:
        node_started_at = now_iso()

        assert context.tool_execution_context is not None
        enforce_node_capabilities(context.tool_execution_context, node)

        deps_satisfied = all(node_status_map.get(dep) == "SUCCESS" for dep in node.depends_on)

        if not deps_satisfied:
            error_msg = f"Node '{node.id}' dependencies not satisfied"
            errors.append(error_msg)
            node_result = {
                "ok": False,
                "errors": [error_msg],
            }
            node_results.append(node_result)
            status = "FAILED"
            node_status_map[node.id] = status

            state_path = write_node_state(
                run_id=run_id,
                node_id=node.id,
                subject="project",
                status=status,
                started_at=node_started_at,
                ended_at=now_iso(),
                result=node_result,
                work_dir=work_dir,
            )
            node_states.append(str(state_path))

            if stop_on_failure:
                break
            continue

        try:
            runner = get_node_runner(node.id)
        except KeyError as exc:
            error_msg = str(exc)
            errors.append(error_msg)
            node_result = {
                "ok": False,
                "errors": [error_msg],
            }
            node_results.append(node_result)
            status = "FAILED"
            node_status_map[node.id] = status

            state_path = write_node_state(
                run_id=run_id,
                node_id=node.id,
                subject="project",
                status=status,
                started_at=node_started_at,
                ended_at=now_iso(),
                result=node_result,
                work_dir=work_dir,
            )
            node_states.append(str(state_path))

            if stop_on_failure:
                break
            continue

        # Check if this is a subject-level node
        parallel_level = node.parallel_level or "project"
        if parallel_level == "subject":
            # Load dataset_index to get subject list
            dataset_index_path = node.params.get("dataset_index")
            subjects = []
            if dataset_index_path and Path(dataset_index_path).exists():
                try:
                    dataset_index = json.loads(Path(dataset_index_path).read_text(encoding="utf-8"))
                    subjects = [
                        s
                        for s in dataset_index.get("subjects", [])
                        if s.get("status") == "COMPLETE"
                    ]
                except (OSError, json.JSONDecodeError):
                    pass

            if not subjects:
                # Try to find smoothed outputs for QC nodes
                if node.id == "subject_qc":
                    # Look for existing smoothed outputs
                    spm_smooth_dir = Path(derivatives_dir) / "spm_smooth"
                    if spm_smooth_dir.exists():
                        for subj_dir in spm_smooth_dir.iterdir():
                            if subj_dir.is_dir() and subj_dir.name.startswith("sub-"):
                                subjects.append({"subject_id": subj_dir.name})

            subjects = filter_recovery_subjects(execution_context.ticket, subjects)

            if not subjects:
                error_msg = f"Node '{node.id}' is subject-level but no COMPLETE subjects found"
                errors.append(error_msg)
                node_result = {
                    "ok": False,
                    "errors": [error_msg],
                }
                node_results.append(node_result)
                status = "FAILED"
                node_status_map[node.id] = status

                state_path = write_node_state(
                    run_id=run_id,
                    node_id=node.id,
                    subject="project",
                    status=status,
                    started_at=node_started_at,
                    ended_at=now_iso(),
                    result=node_result,
                    work_dir=work_dir,
                )
                node_states.append(str(state_path))

                if stop_on_failure:
                    break
                continue

            # Determine worker count for this node
            is_matlab = "matlab" in node.backend
            is_gpu = node.gpu_supported
            if scheduler_mode == "local_parallel":
                if is_matlab:
                    worker_count = matlab_max_workers
                elif is_gpu and gpu_mode != "off":
                    worker_count = gpu_max_workers
                else:
                    worker_count = max_workers
            else:
                worker_count = 1

            # Inject _gpu_info into node params for GPU nodes
            if is_gpu and gpu_mode != "off":
                node.params["_gpu_info"] = gpu_info
                node.params["_gpu_mode"] = gpu_mode

                if gpu_mode == "require" and (
                    gpu_info is None or not gpu_info.get("gpu_available")
                ):
                    error_msg = f"Node '{node.id}' requires GPU but no GPU is available."
                    errors.append(error_msg)
                    node_result = {"ok": False, "errors": [error_msg]}
                    node_results.append(node_result)
                    status = "FAILED"
                    node_status_map[node.id] = status
                    state_path = write_node_state(
                        run_id=run_id,
                        node_id=node.id,
                        subject="project",
                        status=status,
                        started_at=node_started_at,
                        ended_at=now_iso(),
                        result=node_result,
                        work_dir=work_dir,
                    )
                    node_states.append(str(state_path))
                    if stop_on_failure:
                        break
                    continue

            # Filter out subjects that have failed in previous subject-level nodes
            eligible_subjects = [s for s in subjects if s.get("subject_id") not in failed_subjects]

            if worker_count <= 1 or len(eligible_subjects) <= 1:
                # Sequential execution
                all_subject_success = True
                for subject_record in eligible_subjects:
                    subject_id = subject_record.get("subject_id", "unknown")
                    subject_started_at = now_iso()

                    # Persist the factual start before invoking the runner.
                    # If this atomic write cannot be made, execution must not
                    # proceed without a recoverable progress record.
                    write_node_state(
                        run_id=run_id,
                        node_id=node.id,
                        subject=subject_id,
                        status="RUNNING",
                        started_at=subject_started_at,
                        ended_at=None,
                        result={},
                        work_dir=work_dir,
                    )

                    try:
                        node_result = runner(context, node, subject_record, subject_id)
                    except Exception as exc:
                        error_msg = f"Node '{node.id}' execution failed for {subject_id}: {exc}"
                        errors.append(error_msg)
                        node_result = {
                            "ok": False,
                            "subject_id": subject_id,
                            "errors": [error_msg],
                        }
                        all_subject_success = False
                        failed_subjects.add(subject_id)

                    subject_status = determine_status_from_result(node_result)

                    if subject_status == "FAILED":
                        all_subject_success = False
                        failed_subjects.add(subject_id)

                    state_path = write_node_state(
                        run_id=run_id,
                        node_id=node.id,
                        subject=subject_id,
                        status=subject_status,
                        started_at=subject_started_at,
                        ended_at=now_iso(),
                        result=node_result,
                        work_dir=work_dir,
                    )
                    node_states.append(str(state_path))

                    if subject_status == "FAILED" and stop_on_failure:
                        break

                # Aggregate result for the node
                node_result = {
                    "ok": all_subject_success,
                    "node_id": node.id,
                    "subjects_processed": len(eligible_subjects),
                }
                node_results.append(node_result)
                status = "SUCCESS" if all_subject_success else "FAILED"
                node_status_map[node.id] = status

                if status == "FAILED" and stop_on_failure:
                    break
            else:
                # Parallel execution with ThreadPoolExecutor
                all_subject_success = True
                subject_results: list[dict[str, Any]] = []

                def run_one_subject(
                    subject_record: dict[str, Any],
                    *,
                    current_node: PipelineNode = node,
                    current_runner: Any = runner,
                ) -> dict[str, Any]:
                    subject_id = subject_record.get("subject_id", "unknown")
                    subject_started_at = now_iso()

                    write_node_state(
                        run_id=run_id,
                        node_id=current_node.id,
                        subject=subject_id,
                        status="RUNNING",
                        started_at=subject_started_at,
                        ended_at=None,
                        result={},
                        work_dir=context.work_dir,
                    )

                    try:
                        result = current_runner(
                            context, current_node, subject_record, subject_id
                        )
                        result["subject_id"] = subject_id
                        result["started_at"] = subject_started_at
                        result["ended_at"] = now_iso()
                        return result
                    except Exception as exc:
                        return {
                            "ok": False,
                            "subject_id": subject_id,
                            "errors": [
                                f"Node '{current_node.id}' execution failed for "
                                f"{subject_id}: {exc}"
                            ],
                            "started_at": subject_started_at,
                            "ended_at": now_iso(),
                        }

                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = {
                        executor.submit(run_one_subject, subj): subj for subj in eligible_subjects
                    }

                    for future in as_completed(futures):
                        subject_record = futures[future]
                        subject_id = subject_record.get("subject_id", "unknown")

                        try:
                            result = future.result()
                        except Exception as exc:
                            result = {
                                "ok": False,
                                "subject_id": subject_id,
                                "errors": [f"Subject execution failed: {exc}"],
                                "started_at": now_iso(),
                                "ended_at": now_iso(),
                            }

                        subject_results.append(result)
                        subject_status = determine_status_from_result(result)

                        if subject_status == "FAILED":
                            all_subject_success = False
                            failed_subjects.add(subject_id)
                            errors.extend(result.get("errors", []))

                        state_path = write_node_state(
                            run_id=run_id,
                            node_id=node.id,
                            subject=subject_id,
                            status=subject_status,
                            started_at=result.get("started_at", now_iso()),
                            ended_at=result.get("ended_at", now_iso()),
                            result=result,
                            work_dir=work_dir,
                        )
                        node_states.append(str(state_path))

                # Aggregate result for the node
                node_result = {
                    "ok": all_subject_success,
                    "node_id": node.id,
                    "subjects_processed": len(eligible_subjects),
                    "parallel": True,
                    "workers": worker_count,
                }
                node_results.append(node_result)
                status = "SUCCESS" if all_subject_success else "FAILED"
                node_status_map[node.id] = status

                if status == "FAILED" and stop_on_failure:
                    break

        else:
            # Project-level node execution
            write_node_state(
                run_id=run_id,
                node_id=node.id,
                subject="project",
                status="RUNNING",
                started_at=node_started_at,
                ended_at=None,
                result={},
                work_dir=work_dir,
            )
            try:
                node_result = runner(context, node)
            except Exception as exc:
                error_msg = f"Node '{node.id}' execution failed: {exc}"
                errors.append(error_msg)
                node_result = {
                    "ok": False,
                    "errors": [error_msg],
                }

            node_results.append(node_result)
            status = determine_status_from_result(node_result)
            node_status_map[node.id] = status

            state_path = write_node_state(
                run_id=run_id,
                node_id=node.id,
                subject="project",
                status=status,
                started_at=node_started_at,
                ended_at=now_iso(),
                result=node_result,
                work_dir=work_dir,
            )
            node_states.append(str(state_path))

            if status == "FAILED" and stop_on_failure:
                break

    ended_at = now_iso()

    all_success = all(r.get("ok") for r in node_results)
    any_failed = any(not r.get("ok") for r in node_results)

    if all_success:
        pipeline_status = "SUCCESS"
    elif any_failed and len(node_results) < len(pipeline.nodes):
        pipeline_status = "PARTIAL"
    else:
        pipeline_status = "FAILED"

    # Calculate duration
    duration_seconds = _elapsed_seconds(started_at, ended_at)

    summary_path = write_pipeline_summary(
        run_id=run_id,
        pipeline_id=pipeline.pipeline_id,
        status=pipeline_status,
        started_at=started_at,
        ended_at=ended_at,
        node_states=node_states,
        node_results=node_results,
        errors=errors,
        work_dir=work_dir,
        scheduler={
            "mode": scheduler_mode,
            "max_workers": max_workers,
            "matlab_max_workers": matlab_max_workers,
            "gpu_max_workers": gpu_max_workers,
            "gpu_mode": gpu_mode,
        },
        duration_seconds=duration_seconds,
    )

    return {
        "status": pipeline_status,
        "run_id": run_id,
        "pipeline_id": pipeline.pipeline_id,
        "summary_path": str(summary_path),
        "node_states": node_states,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_seconds": duration_seconds,
        "scheduler": {
            "mode": scheduler_mode,
            "max_workers": max_workers,
            "matlab_max_workers": matlab_max_workers,
            "gpu_max_workers": gpu_max_workers,
            "gpu_mode": gpu_mode,
        },
        "errors": errors,
    }
