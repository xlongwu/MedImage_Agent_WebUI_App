from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from src.backend.app.core.exceptions import NotFoundError, SafetyError
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.services.agent_team_read_model import AgentTeamReadModel

NOW = datetime(2026, 10, 13, tzinfo=UTC)


class Store:
    def __init__(self, *, project_id: str = "project-1") -> None:
        self.project_id = project_id
        self.lifecycle = AgentLifecycleRecord(
            lifecycle_id="task-1",
            project_id=project_id,
            state="CONTEXT_READY",
            planning_mode="multi_agent",
            planning_team_id="team-1",
            planning_team_epoch=1,
            created_at=NOW,
            updated_at=NOW,
        )
        self.team = SimpleNamespace(
            team_id="team-1",
            project_id=project_id,
            lifecycle_id="task-1",
            team_epoch=1,
            status="RUNNING",
            budget=SimpleNamespace(max_model_calls=4),
            created_at=NOW,
            updated_at=NOW + timedelta(seconds=5),
        )
        self.items = [
            SimpleNamespace(
                team_id="team-1",
                team_epoch=1,
                project_id=project_id,
                lifecycle_id="task-1",
                reviewer_kind=kind,
                role_id=f"{kind}_reviewer.v1",
                status="COMPLETED",
                error_code=None,
            )
            for kind in ("science", "safety", "completeness")
        ]
        self.calls = [
            SimpleNamespace(
                team_id="team-1",
                team_epoch=1,
                project_id=project_id,
                lifecycle_id="task-1",
                network_called=True,
                input_tokens=10,
                output_tokens=3,
                latency_ms=25,
            ),
            SimpleNamespace(
                team_id="team-1",
                team_epoch=1,
                project_id=project_id,
                lifecycle_id="task-1",
                network_called=False,
                input_tokens=None,
                output_tokens=None,
                latency_ms=None,
            ),
        ]
        self.findings = [
            SimpleNamespace(
                team_id="team-1",
                team_epoch=1,
                project_id=project_id,
                lifecycle_id="task-1",
                finding_id=f"finding-{index}",
                reviewer_kind="science",
                severity="warning",
                code="SCIENCE_EVIDENCE_GAP",
                message_key="agent.review.science_gap",
                input_refs=("reviewed_plan",),
                suggested_change="private model text must not be projected",
                created_at=NOW + timedelta(seconds=index),
            )
            for index in (1, 2)
        ]
        self.events = [
            SimpleNamespace(
                event_id=f"event-{index}",
                team_id="team-1",
                team_epoch=1,
                project_id=project_id,
                lifecycle_id="task-1",
                event_type="MODEL_CALL_COMPLETED",
                occurred_at=NOW + timedelta(seconds=index),
                source="worker",
            )
            for index in (1, 2)
        ]

    def get_project(self, project_id: str):
        return SimpleNamespace(id=self.project_id) if project_id == self.project_id else None

    def get_agent_lifecycle(self, lifecycle_id: str):
        return self.lifecycle if lifecycle_id == self.lifecycle.lifecycle_id else None

    def get_agent_team(self, project_id: str, team_id: str):
        return self.team if project_id == self.project_id and team_id == self.team.team_id else None

    def get_agent_team_advisory(self, project_id: str, team_id: str):
        return SimpleNamespace(
            team_id=team_id,
            team_epoch=1,
            project_id=project_id,
            lifecycle_id="task-1",
            conflict_codes=(),
            advisory_hash="a" * 64,
        )

    def list_agent_team_work_items(self, project_id: str, team_id: str):
        return self.items

    def list_agent_team_model_calls(self, project_id: str, team_id: str):
        return self.calls

    def list_agent_team_findings(self, project_id: str, team_id: str):
        return self.findings

    def list_agent_team_events(self, project_id: str, team_id: str):
        return self.events


def test_team_summary_projects_bounded_role_and_usage_status() -> None:
    summary = AgentTeamReadModel(Store()).summary(project_id="project-1", task_id="task-1")

    assert summary.schema_version == 1
    assert summary.status == "RUNNING"
    assert summary.required_roles == ("science", "safety", "completeness")
    assert summary.completed_roles == ("science", "safety", "completeness")
    assert summary.model_call_count == 1
    assert summary.budget_used == 1
    assert summary.budget_limit == 4
    assert summary.input_tokens == 10
    assert summary.output_tokens == 3


def test_findings_are_project_scoped_paged_and_redacted() -> None:
    store = Store()
    model = AgentTeamReadModel(store)

    first = model.findings(project_id="project-1", task_id="task-1", limit=1)
    second = model.findings(
        project_id="project-1", task_id="task-1", after=first.next_cursor, limit=1
    )

    assert first.items[0].finding_id == "finding-1"
    assert first.next_cursor == "finding-1"
    assert second.items[0].finding_id == "finding-2"
    assert second.next_cursor is None
    assert "suggested_change" not in first.items[0].model_dump()
    with pytest.raises(NotFoundError):
        model.findings(project_id="another-project", task_id="task-1")
    with pytest.raises(SafetyError, match="AGENT_TEAM_FINDING_CURSOR_STALE"):
        model.findings(project_id="project-1", task_id="task-1", after="unknown")


def test_events_use_localizable_messages_and_team_identity() -> None:
    page = AgentTeamReadModel(Store()).events(
        project_id="project-1", task_id="task-1", limit=1
    )

    assert page.items[0].team_id == "team-1"
    assert page.items[0].message.code == "agent.team.event.review_completed"
    assert page.next_cursor == "event-1"
