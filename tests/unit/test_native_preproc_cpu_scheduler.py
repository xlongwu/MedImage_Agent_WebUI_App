from __future__ import annotations

from src.backend.app.native_preproc.orchestrator import resource_planner
from src.backend.app.schemas.native_preproc_api import (
    NativeCpuExecutionPolicy,
    NativeFullPreprocRequest,
)


def test_cpu_policy_defaults_to_bounded_auto_planning() -> None:
    request = NativeFullPreprocRequest()

    assert request.cpu_policy.mode == "auto"


def test_resource_planner_respects_cpu_memory_and_user_ceiling(monkeypatch, tmp_path) -> None:
    bold = tmp_path / "bold.nii.gz"
    bold.write_bytes(b"x" * 1024)
    monkeypatch.setattr(
        resource_planner,
        "capture_resource_snapshot",
        lambda: resource_planner.ResourceSnapshot(
            logical_cpus=12,
            cpu_percent=2.0,
            total_memory_bytes=16 * 1024**3,
            available_memory_bytes=8 * 1024**3,
            source="test",
        ),
    )
    requests = [
        NativeFullPreprocRequest(subject_id=f"sub-{index:03d}", input_bold=str(bold))
        for index in range(3)
    ]

    plan = resource_planner.plan_subject_execution(
        requests,
        NativeCpuExecutionPolicy(mode="process", max_subject_workers=2),
    )

    assert plan.worker_count_used == 2
    assert plan.threads_per_worker_calculated >= 1
    assert "user_worker_ceiling" in plan.limiting_factors


def test_resource_planner_falls_back_to_one_worker_without_memory_probe(monkeypatch) -> None:
    monkeypatch.setattr(
        resource_planner,
        "capture_resource_snapshot",
        lambda: resource_planner.ResourceSnapshot(8, None, None, None, "test"),
    )
    plan = resource_planner.plan_subject_execution(
        [
            NativeFullPreprocRequest(subject_id="sub-001"),
            NativeFullPreprocRequest(subject_id="sub-002"),
        ],
        NativeCpuExecutionPolicy(mode="auto"),
    )

    assert plan.worker_count_used == 1
    assert "memory_probe_unavailable_fallback_serial" in plan.limiting_factors
