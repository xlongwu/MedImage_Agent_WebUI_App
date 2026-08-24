from __future__ import annotations

from pathlib import Path

import numpy as np

from src.backend.app.native_preproc.orchestrator.validation import validate_stage_result_artifacts
from src.backend.app.native_preproc.stages.functional_connectivity import (
    run_functional_connectivity,
)


def test_functional_connectivity_outputs_reloadable_fc_and_fisher_z(tmp_path: Path) -> None:
    timepoints = 24
    times = np.linspace(0.0, 2.0 * np.pi, timepoints, dtype=np.float32)
    roi_ts = np.column_stack([np.sin(times), np.sin(times), -np.sin(times)]).astype(np.float32)
    tsv = tmp_path / "roi_timeseries.tsv"
    rows = ["roi_1_A\troi_2_B\troi_3_C"]
    rows.extend("\t".join(f"{float(value):.8f}" for value in row) for row in roi_ts)
    tsv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = run_functional_connectivity(tsv, tmp_path / "native")

    assert result.status == "warning"
    assert any(warning.startswith("gpu_fallback:") for warning in result.warnings)
    assert validate_stage_result_artifacts(result) == []
    corr = np.load(result.output_artifacts[0].path)
    fisher_z = np.load(result.output_artifacts[1].path)
    assert corr.shape == (3, 3)
    assert fisher_z.shape == (3, 3)
    assert np.allclose(corr, corr.T, atol=1e-6)
    assert np.allclose(np.diag(corr), 1.0, atol=1e-6)
    assert np.allclose(np.diag(fisher_z), 0.0, atol=1e-6)
    assert corr[0, 1] > 0.99
    assert corr[0, 2] < -0.99
    assert result.output_artifacts[0].artifact_type == "fc_matrix"
    assert result.output_artifacts[1].artifact_type == "fisher_z_matrix"


def test_functional_connectivity_warns_for_constant_roi(tmp_path: Path) -> None:
    timepoints = 8
    roi_ts = np.column_stack(
        [np.arange(timepoints, dtype=np.float32), np.ones(timepoints, dtype=np.float32)]
    )
    tsv = tmp_path / "roi_timeseries.tsv"
    rows = ["roi_1_signal\troi_2_constant"]
    rows.extend("\t".join(f"{float(value):.8f}" for value in row) for row in roi_ts)
    tsv.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = run_functional_connectivity(tsv, tmp_path / "native")

    assert result.status == "warning"
    assert result.qc.metrics["constant_roi_count"] == 1
    corr = np.load(result.output_artifacts[0].path)
    assert np.allclose(corr[1, 0], 0.0)
    assert np.allclose(corr[1, 1], 1.0)
