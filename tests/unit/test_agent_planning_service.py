from __future__ import annotations

from src.backend.app.core.config import ConfigService
from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService


def test_planning_service_advances_a_persisted_lifecycle_without_execution_dependencies(tmp_path) -> None:
    """The planning seam may request missing input but cannot dispatch work."""
    store = SQLiteDesktopStore(tmp_path / "planning.sqlite")
    project = ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0,
        total_size="0", current_model_id="none",
    )
    store.add_project(project, health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(
        project_id=project.id, command_id="create-1", actor="researcher", goal_text="Plan ALFF",
        goal_hash="goal-hash", planning_wake_reason="create",
    )
    service = AgentPlanningService(
        store, planner=None, goal_planning_service=GoalPlanningService(), plan_saver=save_reviewed_plan,
        summary_service=ApprovalSummaryService(),
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_influence_guard=MemoryInfluenceGuard(), harness_config=ConfigService().harness,
        evidence_service=AgentEvidenceService(store),
    )

    advanced = service.advance_planning(
        project_id=project.id, lifecycle_id=lifecycle.lifecycle_id, wake_reason="create"
    )

    assert advanced.state == "WAITING_FOR_INPUT"
    assert advanced.execution_ticket_id is None
    assert advanced.run_id is None
