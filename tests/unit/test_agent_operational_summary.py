from __future__ import annotations

from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_operational_summary_service import AgentOperationalSummaryService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def test_summary_is_empty_and_project_scoped_without_writes(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "operations.sqlite")
    for project_id in ("a", "b"):
        store.add_project(ProjectDetail(
            id=project_id, name=project_id, study_id="synthetic", modality="rs-fMRI",
            created_date="today", subjects_count=0, current_pipeline_id="pipeline",
            sequences=[], scans_count=0, total_size="0", current_model_id="none",
        ), health_status="ready", rawdata_dir="")
    summary = AgentOperationalSummaryService(store).build(project_id="a")
    assert summary.project_id == "a"
    assert summary.lifecycle_state_counts == {}
    assert summary.model_call_counts == {}
    assert summary.attentions == ()
