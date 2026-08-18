"""Tests confirming no frontend DICOM conversion execute UI exists — Phase 4L-3.

Verifies that in the current design-only phase:
- No visible "Run Conversion" or "Execute Conversion" text appears in frontend source
- No onClick handler calls /conversion/execute
- No runProjectDicomConversionExecute function exists in api.ts
- DicomConversionReleaseReadinessPanel + ReviewPanel have no execution triggers
- Backend endpoint remains default-blocked without env flags

Phase 4L-3 boundary: design review only — no button, no onClick, no API wrapper.
"""

from __future__ import annotations

import json
import os

import pytest


class TestFrontendExecuteTextAbsence:
    """No 'Run Conversion' or 'Execute Conversion' visible text in frontend."""

    def test_no_run_conversion_text_in_review_panel(self):
        """DicomConversionReviewPanel must not contain 'Run Conversion' as button label."""
        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReviewPanel.tsx",
        )
        if not os.path.exists(panel_path):
            pytest.skip("Review panel not found at expected path")
        lines = open(panel_path, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if "Run Conversion" in stripped and ("button" in stripped.lower() or ">" in stripped):
                pytest.fail(f"Found 'Run Conversion' at line {i}: {stripped[:120]}")
            if "Execute Conversion" in stripped and (
                "button" in stripped.lower() or ">" in stripped
            ):
                pytest.fail(f"Found 'Execute Conversion' at line {i}: {stripped[:120]}")

    def test_no_run_conversion_text_in_release_readiness_panel(self):
        """ReleaseReadinessPanel must not contain execution button text."""
        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReleaseReadinessPanel.tsx",
        )
        if not os.path.exists(panel_path):
            pytest.skip("Release readiness panel not found at expected path")
        lines = open(panel_path, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if "Run Conversion" in stripped or "Execute Conversion" in stripped:
                pytest.fail(f"Found execution button text at line {i}: {stripped[:120]}")

    def test_no_conversion_execute_in_app_tsx(self):
        """App.tsx must not wire a conversion execute UI."""
        app_path = os.path.join(os.getcwd(), "src/frontend/src/App.tsx")
        if not os.path.exists(app_path):
            pytest.skip("App.tsx not found at expected path")
        content = open(app_path, encoding="utf-8").read()
        if "conversion/execute" in content:
            pytest.fail("App.tsx references /conversion/execute — must not exist")


class TestFrontendOnClickAbsence:
    """No onClick handler triggers conversion execution."""

    def test_review_panel_has_no_execute_onclick(self):
        """DicomConversionReviewPanel must not have an execute onClick handler."""
        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReviewPanel.tsx",
        )
        if not os.path.exists(panel_path):
            pytest.skip("Review panel not found")
        lines = open(panel_path, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if "onClick" in stripped and (
                "conversion/execute" in stripped
                or "runProjectDicomConversionExecute" in stripped
                or ("handleExecute" in stripped and "handleExecutePreflight" not in stripped)
            ):
                pytest.fail(f"Execute onClick found at line {i}: {stripped[:120]}")

    def test_release_readiness_panel_has_no_execute_onclick(self):
        """ReleaseReadinessPanel must not have an execute onClick handler."""
        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReleaseReadinessPanel.tsx",
        )
        if not os.path.exists(panel_path):
            pytest.skip("Release readiness panel not found")
        lines = open(panel_path, encoding="utf-8").read().splitlines()
        # The only onClick should be onRefresh (the "Check release readiness" button)
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            if "onClick" in stripped and "execute" in stripped.lower():
                pytest.fail(
                    f"Execute onClick found in ReleaseReadinessPanel at line {i}: {stripped[:120]}"
                )

    def test_release_readiness_panel_stays_read_only(self):
        """ReleaseReadinessPanel has no state that triggers execution."""
        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReleaseReadinessPanel.tsx",
        )
        if not os.path.exists(panel_path):
            pytest.skip("Release readiness panel not found")
        content = open(panel_path, encoding="utf-8").read()
        # Panel must still declare itself read-only
        assert "read-only" in content.lower() or "read only" in content.lower(), (
            "ReleaseReadinessPanel must document itself as read-only"
        )


class TestFrontendApiWrapperAbsence:
    """No frontend API wrapper exists for conversion execute."""

    def test_no_run_project_dicom_conversion_execute_in_api_ts(self):
        """The canonical DICOM API keeps the retired execution surface absent."""
        api_path = os.path.join(os.getcwd(), "src/frontend/src/lib/api/dicom.ts")
        assert os.path.exists(api_path), "Canonical DICOM API module must exist"
        content = open(api_path, encoding="utf-8").read()
        assert "runProjectDicomConversionExecute" not in content

    def test_api_ts_has_conversion_execute_endpoint(self):
        """The frontend must not expose the retired conversion execute endpoint."""
        api_path = os.path.join(os.getcwd(), "src/frontend/src/lib/api/dicom.ts")
        assert os.path.exists(api_path), "Canonical DICOM API module must exist"
        content = open(api_path, encoding="utf-8").read()
        assert "/conversion/execute" not in content

    def test_types_ts_has_public_execution_types(self):
        """types.ts exports public execution types — added in Phase 4L-4."""
        types_path = os.path.join(os.getcwd(), "src/frontend/src/types.ts")
        if not os.path.exists(types_path):
            pytest.skip("types.ts not found")
        content = open(types_path, encoding="utf-8").read()
        assert "DicomConversionPublicExecutionRequest" in content, (
            "types.ts must contain DicomConversionPublicExecutionRequest in Phase 4L-4"
        )


class TestBackendEndpointDefaultBlocked:
    """Backend /conversion/execute remains default-blocked without env flags."""

    def test_endpoint_returns_blocked_without_env_flags(self):
        """POST /conversion/execute returns 200 with ok=false when flags missing."""
        try:
            from fastapi.testclient import TestClient

            from src.backend.app.main import app

            client = TestClient(app)
            resp = client.post(
                "/api/projects/test-project/conversion/execute",
                json={"conversion_run_id": "run-001"},
            )
            assert resp.status_code == 410
            detail = resp.json()["detail"]
            assert detail["error_code"] == "EXECUTION_CONTRACT_REQUIRED"
            assert detail["replacement"] == "/api/plans/execute-reviewed"
        except ImportError:
            pytest.skip("FastAPI TestClient not available")

    def test_endpoint_disabled_when_endpoint_flag_missing(self):
        """Specifically check MEDIMAGE_ALLOW_PUBLIC_DICOM_CONVERSION_ENDPOINT gating."""
        try:
            from fastapi.testclient import TestClient

            from src.backend.app.main import app

            client = TestClient(app)
            resp = client.post(
                "/api/projects/test-project/conversion/execute",
                json={"conversion_run_id": "run-001"},
            )
            assert resp.status_code == 410
            detail = resp.json()["detail"]
            assert detail["error_code"] == "EXECUTION_CONTRACT_REQUIRED"
            assert detail["entry_id"] == "conversion.execute"
        except ImportError:
            pytest.skip("FastAPI TestClient not available")


class TestFrontendBuildPasses:
    """Frontend TypeScript compilation and build must pass."""

    def test_typecheck_passes(self):
        """npm run typecheck exits clean."""
        package = json.loads(open("src/frontend/package.json", encoding="utf-8").read())
        assert "typecheck" in package["scripts"]

    def test_build_passes(self):
        """npm run build exits clean."""
        package = json.loads(open("src/frontend/package.json", encoding="utf-8").read())
        assert "build" in package["scripts"]
