from datetime import UTC, datetime

from tests.unit.test_approval_summary import _native_reviewed, _project
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.schemas.approval_summary import ApprovalSummary
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.system_message import message
from src.backend.app.core.exceptions import SafetyError
from pydantic import ValidationError
import pytest


def test_approval_and_science_decision_have_semantic_content(tmp_path):
    service = ApprovalSummaryService()
    first = service.build(project=_project(tmp_path), reviewed_plan=_native_reviewed(tmp_path, subject_id="sub-001"), now=datetime(2026, 7, 16, tzinfo=UTC))
    payload = first.model_dump(mode="json")
    assert payload["schema_version"] == 6
    assert payload["sections"][0]["summary"]["code"] == "approval.scope.summary"
    assert payload["sections"][0]["summary"]["params"]["subject_ids"] == ["sub-001"]
    items = AgentPlanningService._science_decision_items({"nodes": [], "metadata": {"science_decisions": {"global_signal_regression_required": True}}}, {}, {})
    assert items[0].model_dump(mode="json")["question"]["code"] == "decision.global_signal_regression.question"


def test_semantic_parameters_are_part_of_approval_identity_and_old_contract_rejected(tmp_path):
    now = datetime(2026, 7, 16, tzinfo=UTC)
    service = ApprovalSummaryService()
    first = service.build(project=_project(tmp_path), reviewed_plan=_native_reviewed(tmp_path, subject_id="sub-001"), now=now)
    data = first.model_dump(mode="json")
    changed = first.sections[0].model_copy(update={"summary": message("approval.scope.summary", count=99, subject_ids=("sub-001",))})
    with pytest.raises(SafetyError, match="APPROVAL_SUMMARY_STALE"):
        service.verify(first.model_copy(update={"sections": (changed, *first.sections[1:])}), now=now)
    data["schema_version"] = 5
    with pytest.raises(ValidationError, match="schema_version"):
        ApprovalSummary.model_validate(data)
    with pytest.raises(ValidationError, match="schema_version"):
        AgentLifecycleRecord.model_validate({"schema_version": 5, "project_id": "p", "lifecycle_id": "t"})
    with pytest.raises(ValidationError):
        message("approval.scope.summary", arbitrary_backend_prose="Never display this")
    with pytest.raises(ValidationError):
        message("option.tr.description", value=float("nan"))


def test_resource_and_memory_facts_are_preserved_without_backend_prose(tmp_path):
    from src.backend.app.schemas.memory import MemoryContext
    plan = {"nodes": [], "metadata": {"science_decisions": {"tr_conflict": {"bids": 2.0, "project": 3.0}}}}
    items = AgentPlanningService._science_decision_items(plan, {}, {})
    assert items[0].options[0].description.params.source == "bids"
    assert items[0].options[0].description.params.value == 2.0
    reviewed = _native_reviewed(tmp_path, subject_id="sub-001")
    reviewed.payload["planning_request"] = {"science_answers": {"global_signal_regression": False, "repetition_time": 2.0, "template": "resources/user-template.nii"}}
    summary = ApprovalSummaryService().build(project=_project(tmp_path), reviewed_plan=reviewed)
    confirmed = {item.params.decision_kind: item.params.value for item in summary.science_changes if item.code == "science.confirmed"}
    assert confirmed == {"global_signal_regression": False, "repetition_time": 2.0, "template": "user-template.nii"}
