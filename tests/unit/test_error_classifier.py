from __future__ import annotations

from src.backend.app.tools.error_classifier import classify_error, classify_errors_batch
from src.backend.app.tools.error_kb_validator import validate_error_kb


def test_classify_known_error_matlab():
    result = classify_error("matlab: command not found")
    assert result["classified"] is True
    assert result["category"] == "matlab_missing"
    assert result["retryable"] is False
    assert result["severity"] == "critical"
    assert result["human_action_required"] is True


def test_classify_known_error_spm():
    result = classify_error("Undefined function or variable 'spm' during batch execution")
    assert result["classified"] is True
    assert result["category"] == "spm_path_error"
    assert result["retryable"] is False


def test_classify_known_error_nifti():
    result = classify_error("NIfTI read error: corrupted header in sub-003")
    assert result["classified"] is True
    assert result["category"] == "nifti_io_error"
    assert result["retryable"] is True


def test_classify_unknown_error():
    result = classify_error("something completely unexpected happened here")
    assert result["classified"] is False
    assert result["category"] == "UNKNOWN_ERROR"
    assert result["match_score"] == 0


def test_validate_error_kb():
    result = validate_error_kb()
    assert result["ok"] is True
    assert result["categories_count"] >= 15


def test_classify_batch():
    errors = ["matlab: command not found", "NIfTI read error", "normal message"]
    results = classify_errors_batch(errors)
    assert len(results) == 3
    assert results[0]["category"] == "matlab_missing"
    assert results[1]["category"] == "nifti_io_error"
    assert results[2]["category"] == "UNKNOWN_ERROR"


def test_classify_matlab_exit_code():
    result = classify_error("MATLAB exited with return code 1 in node spm_realign")
    assert result["classified"] is True
    assert result["category"] == "matlab_returncode_nonzero"
    assert result["retryable"] is True


def test_classify_permission_denied():
    result = classify_error("Permission denied: requires approval from the Agent Task approval gate")
    assert result["classified"] is True
    assert result["category"] == "permission_denied"
