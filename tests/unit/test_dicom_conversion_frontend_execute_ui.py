"""Source contracts for the read-only DICOM conversion frontend boundary."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_WORKSPACE = ROOT / "src/frontend/src/features/workspaces/DataConversionWorkspace.tsx"
PREPROCESSING_WORKSPACE = ROOT / "src/frontend/src/features/workspaces/PreprocessingWorkspace.tsx"
DICOM_API = ROOT / "src/frontend/src/lib/api/dicom.ts"
EN_MESSAGES = ROOT / "src/frontend/src/i18n/messages/en.ts"
ZH_MESSAGES = ROOT / "src/frontend/src/i18n/messages/zh-CN.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestReadOnlyConversionEntry:
    def test_raw_dicom_workspace_restores_only_persisted_dry_run(self) -> None:
        source = _read(DATA_WORKSPACE)
        assert "getLatestConversionDryRun" in source
        assert "conversion/dry-run/latest" in _read(DICOM_API)
        assert "runProjectDicomConversionExecute" not in source
        assert "/conversion/execute" not in source

    def test_rawdata_boundary_is_visible_in_both_locales(self) -> None:
        source = _read(PREPROCESSING_WORKSPACE)
        assert 't("preprocessing.rawReadOnly")' in source
        assert "rawdata" in _read(EN_MESSAGES).lower()
        assert "rawdata" in _read(ZH_MESSAGES).lower()

    def test_raw_dicom_blocks_preprocessing_until_registered_input_exists(self) -> None:
        source = _read(PREPROCESSING_WORKSPACE)
        assert 'dataState === "raw_dicom"' in source
        assert 't("preprocessing.blockedDescription")' in source
        assert "hasRegisteredConvertedInput" in source


class TestSafetyInvariants:
    def test_frontend_does_not_expose_conversion_execute_wrapper(self) -> None:
        source = _read(DICOM_API)
        assert "runProjectDicomConversionExecute" not in source
        assert "/conversion/execute" not in source

    def test_conversion_workspace_never_starts_a_local_process(self) -> None:
        source = _read(DATA_WORKSPACE)
        for pattern in ("child_process", "execSync", "spawn(", "subprocess"):
            assert pattern not in source
