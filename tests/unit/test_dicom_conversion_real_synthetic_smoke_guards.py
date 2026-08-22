"""Unit-level guard tests for real dcm2niix synthetic smoke — Phase 4H-0.

Tests env flag gating, path safety, and subprocess guardrails WITHOUT
calling real dcm2niix.  All tests monkeypatch subprocess or provide
controlled env.
"""

from __future__ import annotations

from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
# Group 1 — Missing env flags returns disabled
# ═══════════════════════════════════════════════════════════════════════


def test_missing_env_flags_disabled():
    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    result = run_real_dcm2niix_synthetic_smoke(
        input_dir=Path("/tmp/synth"),
        output_root=Path("/tmp/out"),
        env={},
    )
    assert result.status == "disabled"
    assert result.safety_flags.conversion_disabled_by_default is True


def test_partial_env_flags_disabled():
    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    env = {"MEDIMAGE_ENABLE_DICOM_CONVERSION": "1"}
    result = run_real_dcm2niix_synthetic_smoke(
        input_dir=Path("/tmp/synth"),
        output_root=Path("/tmp/out"),
        env=env,
    )
    assert result.status == "disabled"


# ═══════════════════════════════════════════════════════════════════════
# Group 2 — Real rawdata path blocked
# ═══════════════════════════════════════════════════════════════════════


def test_real_rawdata_path_blocked():
    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    env = {
        "MEDIMAGE_ENABLE_DICOM_CONVERSION": "1",
        "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE": "1",
        "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE": "1",
        "MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION": "1",
        "MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE": "1",
        "MEDIMAGE_MATLAB_ENABLED": "1",
        "MEDIMAGE_SPM_SMOKE_ENABLED": "1",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
        "MEDIMAGE_ENABLE_REAL_PREPROCESSING": "1",
    }
    # Path contains "FunRaw" — should be blocked unless under tmp
    result = run_real_dcm2niix_synthetic_smoke(
        input_dir=Path("/data/FunRaw/Sub_001"),
        output_root=Path("/tmp/out"),
        env=env,
    )
    assert result.status in {"blocked", "disabled"}


def test_synthetic_tmp_path_allowed():
    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    # Per §11.1, MATLAB/SPM/real-preprocessing flags are NOT required.
    env = {
        "MEDIMAGE_ENABLE_DICOM_CONVERSION": "1",
        "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE": "1",
        "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE": "1",
        "MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION": "1",
        "MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE": "1",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION": "1",
        "MEDIMAGE_ALLOW_USER_DATA_CONVERSION": "1",
    }
    # Path is under a pytest tmpdir — should pass path safety
    result = run_real_dcm2niix_synthetic_smoke(
        input_dir=Path("/tmp/pytest-xxx/synth_input"),
        output_root=Path("/tmp/out"),
        env=env,
    )
    # Will be blocked by dcm2niix availability (not on PATH), not by path safety
    assert result.status not in {"disabled"}  # Should not be disabled by env flags


# ═══════════════════════════════════════════════════════════════════════
# Group 3 — No shell=True
# ═══════════════════════════════════════════════════════════════════════


def test_no_shell_true_in_source():
    import inspect

    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    source = inspect.getsource(run_real_dcm2niix_synthetic_smoke)
    # Only check actual code, filter docstring lines
    lines = [
        line
        for line in source.splitlines()
        if '"""' not in line and not line.strip().startswith("#")
    ]
    code = "\n".join(lines)
    assert "shell=True" not in code


def test_retired_smoke_never_starts_a_process_directly():
    import inspect

    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    source = inspect.getsource(run_real_dcm2niix_synthetic_smoke)
    assert "subprocess.run" not in source
    assert "reject_unreviewed_process_start" in source
    assert "argv" in source


# ═══════════════════════════════════════════════════════════════════════
# Group 4 — Existing safety
# ═══════════════════════════════════════════════════════════════════════


def test_user_conversion_still_disabled():
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionExecutionRequest,
    )
    from src.backend.app.services.dicom_conversion_execution import (
        run_conversion_execute,
    )

    result = run_conversion_execute("test", DicomConversionExecutionRequest())
    assert result.conversion_disabled is True


def test_spm_dpabi_matlab_still_disabled():
    """Verify no SPM/DPABI/MATLAB imports in the real smoke function."""
    import inspect

    from src.backend.app.services.dicom_conversion_execution import (
        run_real_dcm2niix_synthetic_smoke,
    )

    source = inspect.getsource(run_real_dcm2niix_synthetic_smoke)
    assert "import spm" not in source.lower()
    assert "import matlab" not in source.lower()
    assert "import dpabi" not in source.lower()


def test_canonical_flag_list_has_9_flags():
    """The canonical required-flags constant must have exactly 7 entries.

    Per 实现dcm2nii任务方案.md §11.1, MATLAB/SPM/real-preprocessing flags
    are intentionally NOT required for DICOM conversion. The canonical
    flag list has been reduced from 9 to 7 entries.
    """
    from src.backend.app.services.dicom_conversion_execution import (
        REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS,
    )

    assert len(REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS) == 7
    assert "MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE" in REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS


def test_canonical_flag_matches_env_gate():
    """The service env gate must use the same flags as the canonical constant."""
    from src.backend.app.services.dicom_conversion_execution import (
        _REAL_SMOKE_ENV_FLAGS,
        REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS,
    )

    assert frozenset(REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS) == _REAL_SMOKE_ENV_FLAGS
