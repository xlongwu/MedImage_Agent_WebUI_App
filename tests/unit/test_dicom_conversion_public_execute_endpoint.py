"""Phase 7 regression tests for the retired public DICOM execute surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.services.mock_store import SQLiteDesktopStore


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"approved": True},
        {
            "approved": True,
            "confirm_user_data_conversion": True,
            "confirm_rawdata_readonly": True,
            "confirm_research_use_only": True,
            "confirm_no_clinical_use": True,
            "confirm_rollback_available": True,
            "confirm_disk_space_checked": True,
            "confirm_public_execution_risk": True,
        },
    ],
)
def test_legacy_conversion_execute_always_requires_reviewed_contract(
    payload, monkeypatch, tmp_path
):
    store = SQLiteDesktopStore(tmp_path / "execution-events.sqlite")
    monkeypatch.setattr(
        "src.backend.app.api.execution_contract.store_module.mock_store",
        store,
    )

    response = TestClient(app).post(
        "/api/projects/public-exec-test/conversion/execute",
        json=payload,
    )

    assert response.status_code == 410
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["status"] == "EXECUTION_CONTRACT_REQUIRED"
    assert detail["entry_id"] == "conversion.execute"
    assert detail["replacement"] == "/api/plans/execute-reviewed"
    assert detail["audit_event_id"].startswith("ticket_event_")


def test_legacy_conversion_execute_does_not_call_conversion_service(monkeypatch):
    called = False

    def forbidden(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("legacy conversion endpoint reached execution service")

    monkeypatch.setattr(
        "src.backend.app.services.dicom_conversion_execution.run_conversion_execute",
        forbidden,
    )
    response = TestClient(app).post(
        "/api/projects/public-exec-test/conversion/execute",
        json={"approved": True},
    )
    assert response.status_code == 410
    assert called is False


def test_frontend_has_no_direct_run_conversion_handler():
    panel = Path("src/frontend/src/components/DicomConversionReviewPanel.tsx")
    if not panel.exists():
        return
    active_lines = [
        line.strip()
        for line in panel.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("//", "/*", "*"))
    ]
    assert not any("onClick" in line and "Run Conversion" in line for line in active_lines)


def test_frontend_legacy_wrapper_cannot_restore_backend_authority():
    api_path = Path("src/frontend/src/lib/api/dicom.ts")
    assert api_path.exists()
    content = api_path.read_text(encoding="utf-8")
    assert "runProjectDicomConversionExecute" not in content
    assert "/conversion/execute" not in content
