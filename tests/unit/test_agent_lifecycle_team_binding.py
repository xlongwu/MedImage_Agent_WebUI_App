from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path: Path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "team-binding.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1",
            name="project-1",
            study_id="study-1",
            modality="rs-fMRI",
            created_date="test",
            subjects_count=0,
            current_pipeline_id="test",
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
        ),
        health_status="Ready",
        rawdata_dir="",
    )
    return store


def _create_lifecycle(store: SQLiteDesktopStore) -> AgentLifecycleRecord:
    return AgentOrchestrator(store).create(
        project_id="project-1", command_id="create-task", actor="test"
    )


def _bind(
    orchestrator: AgentOrchestrator,
    lifecycle_id: str,
    *,
    team_id: str,
    expected_team_id: str | None,
    expected_team_epoch: int,
    command_id: str,
) -> AgentLifecycleRecord:
    return orchestrator.bind_planning_team(
        project_id="project-1",
        lifecycle_id=lifecycle_id,
        team_id=team_id,
        expected_team_id=expected_team_id,
        expected_team_epoch=expected_team_epoch,
        command_id=command_id,
        actor="coordinator",
    )


def test_lifecycle_requires_a_coherent_single_or_multi_team_binding() -> None:
    base = {"schema_version": 7, "lifecycle_id": "task-1", "project_id": "project-1"}
    single = AgentLifecycleRecord(**base)
    assert (single.planning_mode, single.planning_team_id, single.planning_team_epoch) == (
        "single_agent",
        None,
        0,
    )
    with pytest.raises(ValidationError, match="LIFECYCLE_PLANNING_TEAM_BINDING_INVALID"):
        AgentLifecycleRecord(**base, planning_mode="multi_agent")
    with pytest.raises(ValidationError, match="LIFECYCLE_PLANNING_TEAM_BINDING_INVALID"):
        AgentLifecycleRecord(**base, planning_mode="multi_agent", planning_team_id="")
    with pytest.raises(ValidationError, match="LIFECYCLE_PLANNING_TEAM_BINDING_INVALID"):
        AgentLifecycleRecord(**base, planning_team_id="team-1")
    with pytest.raises(ValidationError, match="LIFECYCLE_PLANNING_TEAM_BINDING_INVALID"):
        AgentLifecycleRecord(**base, planning_team_epoch=1)


def test_team_binding_advances_epoch_and_rejects_stale_wake_and_finalize(tmp_path: Path) -> None:
    store = _store(tmp_path)
    orchestrator = AgentOrchestrator(store)
    original = _create_lifecycle(store)

    first = _bind(
        orchestrator,
        original.lifecycle_id,
        team_id="team-1",
        expected_team_id=None,
        expected_team_epoch=0,
        command_id="bind-team-1",
    )
    second = _bind(
        orchestrator,
        original.lifecycle_id,
        team_id="team-2",
        expected_team_id="team-1",
        expected_team_epoch=1,
        command_id="bind-team-2",
    )
    assert (first.planning_team_id, first.planning_team_epoch) == ("team-1", 1)
    assert (second.planning_team_id, second.planning_team_epoch) == ("team-2", 2)

    with pytest.raises(SafetyError, match="TEAM_BINDING_INVALIDATED"):
        _bind(
            orchestrator,
            original.lifecycle_id,
            team_id="late-team",
            expected_team_id="team-1",
            expected_team_epoch=1,
            command_id="stale-wake-bind",
        )

    late_finalize = original.model_copy(
        update={
            "state": "FAILED",
            "updated_at": datetime.now(UTC),
            "last_command_id": "late-finalize",
        }
    )
    late_event = orchestrator._event(
        record=late_finalize,
        command_id="late-finalize",
        actor="worker",
        source_command="late_team_finalize",
        from_state=original.state,
        to_state="FAILED",
    )
    with pytest.raises(RuntimeError, match="LIFECYCLE_CONCURRENT_TRANSITION"):
        store.transition_agent_lifecycle(late_finalize, late_event, expected_state=original.state)

    current = store.get_agent_lifecycle(original.lifecycle_id)
    assert (current.planning_mode, current.planning_team_id, current.planning_team_epoch) == (
        "multi_agent",
        "team-2",
        2,
    )
    assert current.state == original.state


def test_only_epoch_cas_can_change_planning_binding(tmp_path: Path) -> None:
    store = _store(tmp_path)
    orchestrator = AgentOrchestrator(store)
    lifecycle = _create_lifecycle(store)
    with pytest.raises(SafetyError, match="TEAM_BINDING_INVALIDATED"):
        orchestrator.transition(
            project_id="project-1",
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="CONTEXT_READY",
            command_id="ordinary-transition",
            actor="test",
            source_command="ordinary-transition",
            updates={"planning_mode": "multi_agent", "planning_team_id": "team-1"},
        )


def test_two_store_owners_cannot_bind_the_same_epoch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    second_store = SQLiteDesktopStore(store.db_path)
    lifecycle = _create_lifecycle(store)
    barrier = Barrier(2)

    def attempt(owner: int):
        barrier.wait(timeout=5)
        return _bind(
            AgentOrchestrator(store if owner == 0 else second_store),
            lifecycle.lifecycle_id,
            team_id=f"team-{owner}",
            expected_team_id=None,
            expected_team_epoch=0,
            command_id=f"bind-owner-{owner}",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, owner) for owner in (0, 1)]
    outcomes: list[AgentLifecycleRecord | BaseException] = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except BaseException as exc:
            outcomes.append(exc)

    assert sum(isinstance(value, AgentLifecycleRecord) for value in outcomes) == 1
    assert sum(isinstance(value, SafetyError) for value in outcomes) == 1
    current = store.get_agent_lifecycle(lifecycle.lifecycle_id)
    assert current.planning_mode == "multi_agent"
    assert current.planning_team_epoch == 1
    assert current.planning_team_id in {"team-0", "team-1"}


def test_v6_single_agent_lifecycles_are_upgraded_once_to_current_contract(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lifecycle = _create_lifecycle(store)
    legacy = lifecycle.model_dump(mode="json")
    legacy.update(schema_version=6)
    legacy.pop("planning_mode")
    legacy.pop("planning_team_id")
    legacy.pop("planning_team_epoch")
    raw_legacy = json.dumps(legacy, ensure_ascii=False)
    with store._connect() as conn:
        conn.execute(
            "UPDATE agent_lifecycles SET payload=? WHERE lifecycle_id=?",
            (raw_legacy, lifecycle.lifecycle_id),
        )

    reopened = SQLiteDesktopStore(store.db_path)
    migrated = reopened.get_agent_lifecycle(lifecycle.lifecycle_id)
    assert migrated is not None
    assert migrated.schema_version == 7
    assert migrated.planning_mode == "single_agent"
    assert migrated.planning_team_id is None
    assert migrated.planning_team_epoch == 0
    with reopened._connect() as conn:
        stored = json.loads(
            conn.execute(
                "SELECT payload FROM agent_lifecycles WHERE lifecycle_id=?",
                (lifecycle.lifecycle_id,),
            ).fetchone()["payload"]
        )
    assert stored["schema_version"] == 7
    assert stored["planning_mode"] == "single_agent"
