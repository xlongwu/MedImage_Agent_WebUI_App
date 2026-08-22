from __future__ import annotations

import numpy as np
import pytest

from src.backend.app.native_preproc.orchestrator.golden_reference import (
    compare_numeric_reference,
)
from src.backend.app.tools.reho_compute import compute_reho_cupy, compute_reho_numpy


def _require_real_cupy_device():
    cp = pytest.importorskip("cupy")
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            pytest.skip("No CUDA device is available for the ReHo backend comparison.")
    except Exception as exc:
        pytest.skip(f"CUDA runtime is unavailable: {type(exc).__name__}")
    return cp


def test_reho_gpu_matches_tie_corrected_cpu_reference_on_whole_volume() -> None:
    _require_real_cupy_device()
    rng = np.random.default_rng(20260822)
    data = rng.integers(0, 5, size=(6, 6, 6, 12), dtype=np.int16).astype(np.float32)
    mask = np.zeros(data.shape[:3], dtype=bool)
    mask[1:-1, 1:-1, 1:-1] = True

    cpu = compute_reho_numpy(data, neighborhood=7, gm_mask=mask)
    gpu = compute_reho_cupy(data, neighborhood=7, gm_mask=mask, z_chunk_size=2)

    assert cpu["ok"] is True
    assert gpu["ok"] is True
    comparison = compare_numeric_reference(
        gpu["reho"],
        cpu["reho"],
        stage_id="reho",
        metric_name="gpu_cpu_whole_volume_tie_corrected",
        tolerance=1e-5,
        max_abs_tolerance=5e-5,
        min_correlation=0.99999,
        reference_source="canonical_cpu_numpy_backend",
    )
    assert comparison["passed"] is True, comparison
