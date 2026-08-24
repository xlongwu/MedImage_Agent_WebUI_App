"""Contract tests for reviewed GPU policy and backend-neutral native stages."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from src.backend.app.native_preproc.orchestrator import gpu_resource_planner as planner
from src.backend.app.native_preproc.orchestrator.runner import dry_run_native_full_preproc
from src.backend.app.native_preproc.stages.alff_falff import compute_alff_falff_maps
from src.backend.app.native_preproc.stages.atlas_resampling import resample_atlas_with_backend
from src.backend.app.native_preproc.stages.functional_connectivity import (
    compute_roi_functional_connectivity,
)
from src.backend.app.native_preproc.stages.nuisance_regression import regress_confounds_with_backend
from src.backend.app.native_preproc.stages.smoothing import smooth_spatial_with_backend
from src.backend.app.native_preproc.stages.temporal_filtering import temporal_filter_4d
from src.backend.app.schemas.native_preproc_api import NativeComputePolicy, NativeFullPreprocRequest


def _gpu_snapshot(*, free: int = 6 * 1024**3, total: int = 8 * 1024**3) -> dict[str, object]:
    return {
        "cupy_available": True,
        "gpu_available": True,
        "device_name": "Test GPU",
        "free_vram_bytes": free,
        "total_vram_bytes": total,
        "warnings": [],
    }


def test_compute_policy_defaults_to_auto_and_rejects_unknown_stage() -> None:
    assert NativeComputePolicy().backend == "auto"
    with pytest.raises(ValidationError, match="unsupported or non-GPU"):
        NativeComputePolicy(stage_backends={"reho": "gpu"})


def test_gpu_planner_changes_chunk_and_tokens_with_live_vram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(planner, "_live_gpu_snapshot", lambda: _gpu_snapshot())
    policy = NativeComputePolicy(backend="gpu")
    roomy = planner.plan_gpu_stage(
        "temporal_filtering", input_shape=(32, 32, 16, 100), policy=policy, subject_count=4
    )
    monkeypatch.setattr(planner, "_live_gpu_snapshot", lambda: _gpu_snapshot(free=700 * 1024**2))
    constrained = planner.plan_gpu_stage(
        "temporal_filtering", input_shape=(32, 32, 16, 100), policy=policy, subject_count=4
    )

    assert roomy.selected_backend == "gpu"
    assert roomy.chunk_size >= constrained.chunk_size
    assert roomy.gpu_jobs_calculated >= constrained.gpu_jobs_calculated
    assert constrained.selected_backend in {"gpu", "blocked"}


def test_auto_remains_cpu_and_records_visible_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(planner, "_live_gpu_snapshot", lambda: _gpu_snapshot())
    plan = planner.plan_gpu_stage(
        "alff", input_shape=(4, 4, 4, 20), policy=NativeComputePolicy(backend="auto")
    )
    assert plan.selected_backend == "cpu"
    assert "auto_gpu_not_released_for_stage" in plan.limiting_factors
    assert plan.fallback_allowed is True


def test_require_gpu_does_not_silently_fallback_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        planner,
        "_live_gpu_snapshot",
        lambda: {"cupy_available": False, "gpu_available": False, "warnings": ["missing"]},
    )
    plan = planner.plan_gpu_stage(
        "alff", input_shape=(4, 4, 4, 20), policy=NativeComputePolicy(backend="gpu")
    )
    assert plan.selected_backend == "blocked"
    assert plan.blocking_issues


def test_cpu_reference_stage_outputs_are_preserved_with_default_policy() -> None:
    rng = np.random.default_rng(42)
    bold = rng.normal(size=(3, 3, 2, 16)).astype(np.float32)
    alff_default, falff_default, qc_default, _ = compute_alff_falff_maps(bold, tr=2.0)
    alff_cpu, falff_cpu, qc_cpu, _ = compute_alff_falff_maps(
        bold, tr=2.0, compute_policy=NativeComputePolicy(backend="cpu")
    )
    assert np.array_equal(alff_default, alff_cpu)
    assert np.array_equal(falff_default, falff_cpu)
    assert qc_cpu["compute"]["actual_backend"] == "cpu-numpy"
    assert qc_default["compute"]["actual_backend"] == "cpu-numpy"
    assert qc_cpu["compute"]["runtime"]["total_seconds"] >= 0.0


def test_tier_one_cpu_paths_record_actual_backend_and_keep_numerics() -> None:
    rng = np.random.default_rng(8)
    bold = rng.normal(size=(2, 2, 2, 12)).astype(np.float32)
    filtered, filter_qc = temporal_filter_4d(bold, tr=2.0, compute_policy=NativeComputePolicy())
    assert filtered.shape == bold.shape
    assert filter_qc["compute"]["actual_backend"] == "cpu-numpy"
    assert filter_qc["compute"]["runtime"]["total_seconds"] >= 0.0

    design = np.column_stack([np.ones(12), np.linspace(-1.0, 1.0, 12)]).astype(np.float32)
    residual, regression_provenance = regress_confounds_with_backend(bold, design)
    assert residual.shape == bold.shape
    assert regression_provenance["actual_backend"] == "cpu-numpy"
    assert regression_provenance["runtime"]["total_seconds"] >= 0.0

    roi = rng.normal(size=(12, 3)).astype(np.float32)
    corr, fisher_z, fc_qc, _ = compute_roi_functional_connectivity(roi)
    assert corr.shape == (3, 3)
    assert fisher_z.shape == (3, 3)
    assert fc_qc["compute"]["actual_backend"] == "cpu-numpy"
    assert fc_qc["compute"]["runtime"]["total_seconds"] >= 0.0

    smoothed, smoothing_provenance = smooth_spatial_with_backend(bold, (1.0, 1.0, 1.0))
    assert smoothed.shape == bold.shape
    assert smoothing_provenance["runtime"]["total_seconds"] >= 0.0

    atlas = np.arange(8, dtype=np.int16).reshape((2, 2, 2))
    resampled, atlas_provenance = resample_atlas_with_backend(
        atlas, np.eye(4), atlas.shape, np.eye(4)
    )
    assert np.array_equal(resampled, atlas)
    assert atlas_provenance["runtime"]["total_seconds"] >= 0.0


def test_dry_run_exposes_a_compute_plan_for_every_native_stage() -> None:
    response = dry_run_native_full_preproc(
        "gpu-plan-test",
        NativeFullPreprocRequest(),
    )
    assert response.stage_results
    assert all("compute_plan" in stage.result for stage in response.stage_results)
    alff = next(stage for stage in response.stage_results if stage.stage_id == "alff")
    assert alff.result["compute_plan"]["requested_backend"] == "auto"
    assert alff.result["compute_plan"]["selected_backend"] == "cpu"
    assert "auto_gpu_not_released_for_stage" in alff.result["compute_plan"]["limiting_factors"]
