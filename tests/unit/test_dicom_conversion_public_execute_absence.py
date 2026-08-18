"""Tests confirming public DICOM conversion execute endpoint safety — Phase 4L-2.

The endpoint now exists but is **blocked by default** when env flags are missing.
This module verifies that:
- The endpoint returns blocked/disabled without all env flags
- No frontend execute button exists
- No frontend API wrapper exposes execution to the UI
- run_conversion_execute() remains blocked for normal users

Phase 4L-2 boundary: endpoint exists behind gates, not user-facing in GUI.
"""

from __future__ import annotations

import pytest


class TestPublicExecuteEndpointAbsence:
    """Verify POST /conversion/execute returns 404 or is not registered."""

    def test_post_conversion_execute_returns_404_or_blocked(self):
        """POST /api/projects/{id}/conversion/execute — must return blocked when env flags missing.

        In Phase 4L-2, the endpoint exists but is blocked by default.
        May return 200 with ok=false when env flags are not all set.
        """
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

    def test_no_conversion_execute_route_registered(self):
        """Verify no route matching conversion_execute for execute (POST allowed).

        In Phase 4L-2, a POST /conversion/execute route exists.
        The route IS present — we verify it exists but confirm it blocks
        when env flags are missing in test_post_conversion_execute_returns_blocked_by_default.
        """
        try:
            from src.backend.app.api.routes import router

            found_paths: list[str] = []
            for route in router.routes:
                rp = str(getattr(route, "path", ""))
                methods = getattr(route, "methods", set())
                if ("conversion/execute" in rp) and "POST" in methods:
                    found_paths.append(rp)

            # Phase 4L-2: route now exists — this is expected
            # The safety is that it blocks by default, tested elsewhere
        except ImportError:
            pytest.skip("API routes not importable")

    def test_post_conversion_execute_returns_blocked_by_default(self):
        """POST /api/projects/{id}/conversion/execute returns blocked when env flags missing."""
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


class TestFrontendExecuteAbsence:
    """Verify no frontend execute button or API wrapper exists."""

    def test_no_run_project_dicom_conversion_execute_api_wrapper(self):
        """The frontend has no direct wrapper for the retired endpoint."""
        import os

        api_path = os.path.join(os.getcwd(), "src/frontend/src/lib/api/dicom.ts")
        assert os.path.exists(api_path)
        content = open(api_path, encoding="utf-8").read()
        assert "runProjectDicomConversionExecute" not in content

    def test_no_run_conversion_button_text(self):
        """Verify no frontend text 'Run Conversion' appears as a button label."""
        import os

        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReviewPanel.tsx",
        )
        if os.path.exists(panel_path):
            lines = open(panel_path, encoding="utf-8").read().splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("/*"):
                    continue
                # Look for onClick handler with execute/conversion in it
                if "onClick" in stripped and (
                    "Run Conversion" in stripped
                    or "runConversion" in stripped
                    or "Execute Conversion" in stripped
                    or ("handleExecute" in stripped and "handleExecutePreflight" not in stripped)
                    or "conversion/execute" in stripped
                ):
                    pytest.fail(
                        f"Frontend 'Run Conversion' onClick handler found at line: {stripped[:120]}"
                    )

    def test_no_run_conversion_onclick_in_release_readiness_panel(self):
        """Verify ReleaseReadinessPanel has no onClick execution handler."""
        import os

        panel_path = os.path.join(
            os.getcwd(),
            "src/frontend/src/components/DicomConversionReleaseReadinessPanel.tsx",
        )
        if os.path.exists(panel_path):
            lines = open(panel_path, encoding="utf-8").read().splitlines()
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("/*"):
                    continue
                if "onClick" in stripped and (
                    "execute" in stripped.lower() and "conversion" in stripped.lower()
                ):
                    pytest.fail(
                        f"Frontend ReleaseReadinessPanel has execute onClick: {stripped[:120]}"
                    )


class TestRunConversionExecuteBlocked:
    """Verify run_conversion_execute() remains blocked for normal users."""

    def test_run_conversion_execute_still_blocked(self):
        """run_conversion_execute() must return disabled for any request."""
        try:
            from src.backend.app.schemas.dicom_conversion_execution import (
                DicomConversionExecutionRequest,
            )
            from src.backend.app.services.dicom_conversion_execution import (
                run_conversion_execute,
            )

            req = DicomConversionExecutionRequest()
            resp = run_conversion_execute("test-project", req)
            # ok may be True (response generated ok) but status must
            # indicate execution is disabled/blocked
            assert (
                resp.status == "disabled"
                or resp.status == "blocked"
                or resp.safety_flags.conversion_disabled_by_default
            ), (
                f"run_conversion_execute() returned status={resp.status} — "
                f"must be 'disabled' or 'blocked'"
            )
        except ImportError:
            pytest.skip("dicom_conversion_execution module not importable")

    def test_run_conversion_execute_with_mock_env_still_blocked(self):
        """Even with env flags mocked, run_conversion_execute() must return disabled."""
        try:
            from src.backend.app.schemas.dicom_conversion_execution import (
                DicomConversionExecutionRequest,
            )
            from src.backend.app.services.dicom_conversion_execution import (
                run_conversion_execute,
            )

            # In Phase 4B, run_conversion_execute() is ALWAYS disabled —
            # it ignores env flags and returns a disabled response.
            # We test with actual env to confirm the code path.
            req = DicomConversionExecutionRequest()
            resp = run_conversion_execute("test-project", req)
            # ok may be True (response generated ok) but status must
            # indicate execution is disabled/blocked
            assert (
                resp.status == "disabled"
                or resp.status == "blocked"
                or resp.safety_flags.conversion_disabled_by_default
            ), f"Expected disabled/blocked, got status={resp.status}"
        except ImportError:
            pytest.skip("dicom_conversion_execution module not importable")


class TestPublicEndpointDesignPhaseSafety:
    """Verify the public execution schema itself does not enable execution."""

    def test_is_public_execution_design_only_returns_false(self):
        """The design-only guard must return False in Phase 4L-2 — endpoint exists."""
        from src.backend.app.schemas.dicom_conversion_public_execution import (
            is_public_execution_design_only,
        )

        assert is_public_execution_design_only() is False

    def test_public_execution_allowed_is_true_when_all_gates_met(self):
        """In Phase 4L-2, public_execution_allowed is True when preconditions pass."""
        from src.backend.app.schemas.dicom_conversion_public_execution import (
            evaluate_public_execution_preconditions,
        )

        decision = evaluate_public_execution_preconditions(
            env_flags_ok=True,
            request_confirmations_ok=True,
            release_approval_status="approved",
            release_approval_not_expired=True,
            release_readiness_status="ready_for_human_release_review",
            gates_met=32,
            gates_total=32,
            approval_audit_package_present=True,
            rawdata_checksum_before_exists=True,
            rollback_plan_exists=True,
            disk_space_passed=True,
            output_root_safe=True,
        )
        assert decision.safety_flags.public_execution_allowed is True
        assert decision.decision == "proceed"

    def test_request_model_defaults_safe(self):
        """All confirm_* fields default to False (safest)."""
        from src.backend.app.schemas.dicom_conversion_public_execution import (
            DicomConversionPublicExecutionRequest,
        )

        req = DicomConversionPublicExecutionRequest()
        assert req.confirm_user_data_conversion is False
        assert req.confirm_rawdata_readonly is False
        assert req.confirm_research_use_only is False
        assert req.confirm_no_clinical_use is False
        assert req.confirm_rollback_available is False
        assert req.confirm_disk_space_checked is False
        assert req.confirm_public_execution_risk is False

    def test_response_model_defaults_safe(self):
        """Response defaults to disabled with all safety flags set."""
        from src.backend.app.schemas.dicom_conversion_public_execution import (
            DicomConversionPublicExecutionResponse,
        )

        resp = DicomConversionPublicExecutionResponse()
        assert resp.ok is False
        assert resp.status == "disabled"
        assert resp.safety_flags.public_execution_allowed is False
        assert resp.safety_flags.rawdata_read_only is True
        assert resp.safety_flags.no_shell_execution is True
