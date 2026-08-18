from __future__ import annotations

from pathlib import Path

ROOT = Path.cwd()


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_native_preproc_frontend_exposes_read_only_evidence_without_execution() -> None:
    preprocessing_api = _read("src/frontend/src/lib/api/preprocessing.ts")
    re_exports = _read("src/frontend/src/lib/api/legacy_re_exports.ts")
    combined = preprocessing_api + re_exports

    assert "getLatestNativeFullPreprocessingRun" in combined

    assert "/preprocessing/native/runs/latest" in preprocessing_api
    assert "executeNativeFullPreprocessing" not in combined
    assert "submitNativeFullPreprocessing" not in combined
    assert "executeReviewedPreprocessingPipeline" not in combined
    assert "/preprocessing/native/full/execute" not in preprocessing_api
    assert "/execute-reviewed" not in preprocessing_api


def test_native_preproc_frontend_types_expose_truthful_status_fields() -> None:
    types = _read("src/frontend/src/types.ts")

    assert "NativeFullPreprocRequest" in types
    assert "NativeFullPreprocResponse" in types
    assert "blocked_stages" in types
    assert "metadata_only_stages" in types
    assert "validation_report_path" in types
    assert "final_report_path" in types
    assert "confirm_no_external_tools" in types
