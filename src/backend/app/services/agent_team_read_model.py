"""Project-scoped, redacted read projections for Agent Task Teams."""

from __future__ import annotations

from src.backend.app.core.exceptions import NotFoundError, SafetyError
from src.backend.app.schemas.agent_task import (
    AgentTaskTeamEvent,
    AgentTaskTeamEventPage,
    AgentTaskTeamFinding,
    AgentTaskTeamFindingPage,
    AgentTaskTeamRole,
    AgentTaskTeamSummary,
)
from src.backend.app.schemas.system_message import message


class AgentTeamReadStore:
    def get_project(self, project_id: str): ...
    def get_agent_lifecycle(self, lifecycle_id: str): ...
    def get_agent_team(self, project_id: str, team_id: str): ...
    def get_agent_team_advisory(self, project_id: str, team_id: str): ...
    def list_agent_team_work_items(self, project_id: str, team_id: str): ...
    def list_agent_team_model_calls(self, project_id: str, team_id: str): ...
    def list_agent_team_findings(self, project_id: str, team_id: str): ...
    def list_agent_team_events(self, project_id: str, team_id: str): ...


_ROLE_ORDER = ("science", "safety", "completeness")
_EVENT_MESSAGES = {
    "TEAM_REQUESTED": "agent.team.event.requested",
    "TEAM_GATE_ACCEPTED": "agent.team.event.gate_accepted",
    "TEAM_GATE_REJECTED": "agent.team.event.gate_rejected",
    "TEAM_CONTEXT_FROZEN": "agent.team.event.context_frozen",
    "WORK_ITEM_CLAIMED": "agent.team.event.review_started",
    "MODEL_CALL_STARTED": "agent.team.event.review_started",
    "MODEL_CALL_COMPLETED": "agent.team.event.review_completed",
    "MODEL_CALL_FAILED": "agent.team.event.review_failed",
    "MODEL_CALL_INDETERMINATE": "agent.team.event.review_indeterminate",
    "FINDING_ACCEPTED": "agent.team.event.finding_accepted",
    "FINDING_REJECTED": "agent.team.event.finding_rejected",
    "TEAM_CONFLICT_DETECTED": "agent.team.event.conflict_detected",
    "TEAM_ADVISORY_FROZEN": "agent.team.event.advisory_frozen",
    "TEAM_NEEDS_ATTENTION": "agent.team.event.needs_attention",
    "TEAM_FAILED": "agent.team.event.failed",
    "TEAM_CANCELED": "agent.team.event.canceled",
    "TEAM_BINDING_INVALIDATED": "agent.team.event.binding_invalidated",
    "TEAM_BOUND_TO_PLANNING_REQUEST": "agent.team.event.bound_to_planning",
}


class AgentTeamReadModel:
    def __init__(self, store: AgentTeamReadStore) -> None:
        self.store = store

    def summary(self, *, project_id: str, task_id: str) -> AgentTaskTeamSummary:
        summary = self.summary_for_lifecycle(self._lifecycle(project_id, task_id))
        if summary is None:
            raise NotFoundError("AGENT_TEAM_NOT_FOUND", code="AGENT_TEAM_NOT_FOUND")
        return summary

    def summary_for_lifecycle(self, lifecycle) -> AgentTaskTeamSummary | None:
        team_id = lifecycle.planning_team_id
        if lifecycle.planning_mode != "multi_agent" or not team_id:
            return None
        team = self.store.get_agent_team(lifecycle.project_id, team_id)
        if (
            team is None
            or team.project_id != lifecycle.project_id
            or team.lifecycle_id != lifecycle.lifecycle_id
            or team.team_id != lifecycle.planning_team_id
            or team.team_epoch != lifecycle.planning_team_epoch
        ):
            raise SafetyError(
                "AGENT_TEAM_READ_MODEL_BINDING_INVALID",
                code="AGENT_TEAM_READ_MODEL_BINDING_INVALID",
            )
        items = self.store.list_agent_team_work_items(lifecycle.project_id, team_id)
        calls = self.store.list_agent_team_model_calls(lifecycle.project_id, team_id)
        findings = self.store.list_agent_team_findings(lifecycle.project_id, team_id)
        advisory = self.store.get_agent_team_advisory(lifecycle.project_id, team_id)
        self._assert_records_bound(lifecycle, team, items)
        self._assert_records_bound(lifecycle, team, calls)
        self._assert_records_bound(lifecycle, team, findings)
        if advisory is not None and not self._record_matches_lifecycle(advisory, lifecycle, team):
            raise SafetyError(
                "AGENT_TEAM_READ_MODEL_BINDING_INVALID",
                code="AGENT_TEAM_READ_MODEL_BINDING_INVALID",
            )
        items_by_role = {item.reviewer_kind: item for item in items}
        roles = tuple(
            AgentTaskTeamRole(
                reviewer_kind=kind,
                role_id=items_by_role[kind].role_id,
                status=items_by_role[kind].status,
                error_code=items_by_role[kind].error_code,
            )
            for kind in _ROLE_ORDER
            if kind in items_by_role
        )
        completed = tuple(role.reviewer_kind for role in roles if role.status == "COMPLETED")
        failed = tuple(
            role.reviewer_kind
            for role in roles
            if role.status in {"FAILED", "INDETERMINATE"}
        )
        attention_code = None
        if team.status == "NEEDS_ATTENTION":
            attention_code = next(
                (role.error_code for role in roles if role.error_code),
                "AGENT_TEAM_REQUIRES_ATTENTION",
            )
        return AgentTaskTeamSummary(
            schema_version=1,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            status=team.status,
            roles=roles,
            required_roles=_ROLE_ORDER,
            completed_roles=completed,
            failed_roles=failed,
            blocking_finding_count=sum(item.severity == "blocking" for item in findings),
            warning_finding_count=sum(item.severity == "warning" for item in findings),
            conflict_count=len(advisory.conflict_codes) if advisory is not None else 0,
            model_call_count=sum(call.network_called for call in calls),
            budget_used=sum(call.network_called for call in calls),
            budget_limit=team.budget.max_model_calls,
            input_tokens=sum(call.input_tokens or 0 for call in calls),
            output_tokens=sum(call.output_tokens or 0 for call in calls),
            latency_ms=sum(call.latency_ms or 0 for call in calls),
            started_at=team.created_at,
            updated_at=team.updated_at,
            completed_at=team.updated_at if team.status == "COMPLETED" else None,
            advisory_hash=advisory.advisory_hash if advisory is not None else None,
            attention_code=attention_code,
        )

    def findings(
        self,
        *,
        project_id: str,
        task_id: str,
        after: str | None = None,
        limit: int = 50,
    ) -> AgentTaskTeamFindingPage:
        lifecycle = self._lifecycle(project_id, task_id)
        team = self._team(lifecycle)
        findings = sorted(
            self.store.list_agent_team_findings(project_id, team.team_id),
            key=lambda item: (item.created_at, item.finding_id),
        )
        self._assert_records_bound(lifecycle, team, findings)
        start = self._cursor_start(
            [item.finding_id for item in findings], after, "AGENT_TEAM_FINDING_CURSOR_STALE"
        )
        selected = findings[start : start + limit]
        end = start + len(selected)
        return AgentTaskTeamFindingPage(
            items=tuple(
                AgentTaskTeamFinding(
                    finding_id=item.finding_id,
                    reviewer_kind=item.reviewer_kind,
                    severity=item.severity,
                    code=item.code,
                    message_key=item.message_key,
                    input_refs=item.input_refs,
                )
                for item in selected
            ),
            next_cursor=selected[-1].finding_id if selected and end < len(findings) else None,
        )

    def events(
        self,
        *,
        project_id: str,
        task_id: str,
        after: str | None = None,
        limit: int = 50,
    ) -> AgentTaskTeamEventPage:
        lifecycle = self._lifecycle(project_id, task_id)
        team = self._team(lifecycle)
        events = sorted(
            self.store.list_agent_team_events(project_id, team.team_id),
            key=lambda item: (item.occurred_at, item.event_id),
        )
        self._assert_records_bound(lifecycle, team, events)
        start = self._cursor_start(
            [item.event_id for item in events], after, "AGENT_TEAM_EVENT_CURSOR_STALE"
        )
        selected = events[start : start + limit]
        end = start + len(selected)
        return AgentTaskTeamEventPage(
            items=tuple(
                AgentTaskTeamEvent(
                    event_id=item.event_id,
                    team_id=item.team_id,
                    event_type=item.event_type,
                    occurred_at=item.occurred_at,
                    source=item.source,
                    message=message(
                        _EVENT_MESSAGES.get(item.event_type, "agent.team.event.updated")
                    ),
                )
                for item in selected
            ),
            next_cursor=selected[-1].event_id if selected and end < len(events) else None,
        )

    def _lifecycle(self, project_id: str, task_id: str):
        if self.store.get_project(project_id) is None:
            raise NotFoundError("PROJECT_NOT_FOUND", code="PROJECT_NOT_FOUND")
        lifecycle = self.store.get_agent_lifecycle(task_id)
        if lifecycle is None or lifecycle.project_id != project_id:
            raise NotFoundError("AGENT_TASK_NOT_FOUND", code="AGENT_TASK_NOT_FOUND")
        return lifecycle

    def _team(self, lifecycle):
        team_id = lifecycle.planning_team_id
        if lifecycle.planning_mode != "multi_agent" or not team_id:
            raise NotFoundError("AGENT_TEAM_NOT_FOUND", code="AGENT_TEAM_NOT_FOUND")
        team = self.store.get_agent_team(lifecycle.project_id, team_id)
        if (
            team is None
            or team.project_id != lifecycle.project_id
            or team.team_id != team_id
            or team.lifecycle_id != lifecycle.lifecycle_id
            or team.team_epoch != lifecycle.planning_team_epoch
        ):
            raise NotFoundError("AGENT_TEAM_NOT_FOUND", code="AGENT_TEAM_NOT_FOUND")
        return team

    @staticmethod
    def _assert_records_bound(lifecycle, team, records) -> None:
        if any(
            not AgentTeamReadModel._record_matches_lifecycle(record, lifecycle, team)
            for record in records
        ):
            raise SafetyError(
                "AGENT_TEAM_READ_MODEL_BINDING_INVALID",
                code="AGENT_TEAM_READ_MODEL_BINDING_INVALID",
            )

    @staticmethod
    def _record_matches_lifecycle(record, lifecycle, team) -> bool:
        return (
            record.project_id == lifecycle.project_id
            and record.team_id == team.team_id
            and record.lifecycle_id == lifecycle.lifecycle_id
            and record.team_epoch == team.team_epoch
        )

    @staticmethod
    def _cursor_start(identifiers: list[str], after: str | None, stale_code: str) -> int:
        if after is None:
            return 0
        matches = [index for index, identifier in enumerate(identifiers) if identifier == after]
        if len(matches) != 1:
            raise SafetyError(stale_code, code=stale_code, status_code=400)
        return matches[0] + 1
