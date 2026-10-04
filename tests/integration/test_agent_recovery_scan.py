"""Recovery pages are durable control-plane work, never new execution."""
from datetime import UTC, datetime
from threading import Event

import pytest

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_execution_coordinator import AgentExecutionCoordinator
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_task_scheduler import RecordingPlanningService, _store


def _insert(store, index, *, state="CREATED", project_id="project-1"):
    if store.get_project(project_id) is None:
        store.add_project(ProjectDetail(
            id=project_id, name="fixture", study_id="synthetic", modality="rs-fMRI",
            created_date="fixture", subjects_count=0, current_pipeline_id="none",
            sequences=[], scans_count=0, total_size="0", current_model_id="none",
        ), health_status="ready", rawdata_dir="")
    record = AgentLifecycleRecord(
        lifecycle_id=f"scan-{index:04d}", project_id=project_id, state=state,
        run_id=f"original-{index}" if state == "RUNNING" else None,
    )
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO agent_lifecycles VALUES (?, ?, ?, ?, ?, ?)",
            (record.lifecycle_id, record.project_id, record.state,
             store._dump_model(record), record.created_at.isoformat(), record.updated_at.isoformat()),
        )
    return record


@pytest.mark.parametrize("separate_projects", [False, True])
def test_scan_covers_205_lifecycles_with_bounded_sql_and_reopen(tmp_path, separate_projects):
    store = _store(tmp_path)
    for i in range(205):
        _insert(store, i, project_id=f"project-{i}" if separate_projects else "project-1")
    sql = []
    original = store._connect
    from contextlib import contextmanager

    @contextmanager
    def traced():
        with original() as conn:
            conn.set_trace_callback(sql.append)
            yield conn

    store._connect = traced
    page = store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert len(page.lifecycle_ids) == 100 and page.has_more
    reopened = SQLiteDesktopStore(store.db_path)
    second = reopened.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    third = reopened.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert len(second.lifecycle_ids) == 100 and third.lifecycle_ids == tuple(f"scan-{i:04d}" for i in range(200, 205))
    assert not third.has_more
    assert len(set(page.lifecycle_ids + second.lifecycle_ids + third.lifecycle_ids)) == 205
    assert any("LIMIT 100" in statement and "ORDER BY lifecycle_id" in statement for statement in sql)
    assert reopened.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC)).lifecycle_ids == ()


def test_scan_commit_failure_rolls_back_wakes_and_cursor(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _insert(store, 1)
    original = store._insert_agent_task_wake

    def fail(conn, record):
        original(conn, record)
        raise RuntimeError("crash after wake before cursor")

    monkeypatch.setattr(store, "_insert_agent_task_wake", fail)
    with pytest.raises(RuntimeError, match="crash"):
        store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    reopened = SQLiteDesktopStore(store.db_path)
    assert reopened.list_agent_task_wakes(project_id="project-1") == []
    assert reopened.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC)).lifecycle_ids == ("scan-0001",)


def test_new_lower_id_is_reached_in_next_cycle_and_scope_is_isolated(tmp_path):
    store = _store(tmp_path)
    _insert(store, 1)
    _insert(store, 2, project_id="other")
    page = store.scan_agent_recovery_page(consumer="planning", project_id="project-1", now=datetime.now(UTC))
    assert page.lifecycle_ids == ("scan-0001",)
    _insert(store, 0)
    assert store.scan_agent_recovery_page(consumer="planning", project_id="project-1", now=datetime.now(UTC)).lifecycle_ids == ("scan-0000",)
    assert store.list_agent_task_wakes(project_id="other") == []


def test_worker_continues_tail_without_new_user_command(tmp_path):
    store = _store(tmp_path)
    for i in range(205):
        _insert(store, i)
    done = Event()

    class Planner(RecordingPlanningService):
        def advance_planning(self, **kwargs):
            super().advance_planning(**kwargs)
            if len(self.calls) == 205:
                done.set()

    planner = Planner()
    scheduler = AgentTaskScheduler(store, planning_service=planner)
    try:
        scheduler.rescan()
        assert done.wait(15), len(planner.calls)
        assert len({call[1] for call in planner.calls}) == 205
    finally:
        assert scheduler.shutdown()


def test_execution_scan_only_registers_original_run_or_intermediate_checkpoint(tmp_path):
    store = _store(tmp_path)
    records = [_insert(store, i, state="RUNNING") for i in range(205)]
    _insert(store, 300, state="OBSERVING")
    _insert(store, 301, state="EVALUATING")
    _insert(store, 302, state="WAITING_FOR_APPROVAL")
    now = datetime.now(UTC)
    pages = [store.scan_agent_recovery_page(consumer="execution", now=now) for _ in range(3)]
    assert len(set(sum((p.lifecycle_ids for p in pages), ()))) == 207
    wakes = store.list_agent_execution_wakes(project_id="project-1")
    assert {w.run_id for w in wakes if w.run_id} == {r.run_id for r in records}
    assert len(wakes) == 207
    assert store.list_execution_tickets("project-1") == []
    assert store.list_run_links("project-1") == []


def test_cursor_write_failure_rolls_back_entire_page_and_reopen_continues(tmp_path):
    import sqlite3
    store = _store(tmp_path)
    _insert(store, 1)
    with store._connect() as conn:
        conn.execute("""CREATE TRIGGER fail_scan BEFORE INSERT ON agent_recovery_scans
                        BEGIN SELECT RAISE(ABORT, 'cursor write interrupted'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="cursor write interrupted"):
        store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert store.list_agent_task_wakes(project_id="project-1") == []
    with store._connect() as conn:
        conn.execute("DROP TRIGGER fail_scan")
    reopened = SQLiteDesktopStore(store.db_path)
    assert reopened.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC)).lifecycle_ids == ("scan-0001",)


def test_cycle_upper_bound_defers_new_high_and_low_ids_until_next_cycle(tmp_path):
    store = _store(tmp_path)
    for i in range(1, 102):
        _insert(store, i)
    store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    _insert(store, 0)
    _insert(store, 200)
    tail = store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert tail.lifecycle_ids == ("scan-0101",) and not tail.has_more
    next_page = store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert next_page.lifecycle_ids == ("scan-0000",)
    assert next_page.has_more
    assert store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC)).lifecycle_ids == ("scan-0200",)


def test_execution_worker_reaches_tail_once_without_new_dispatch(tmp_path):
    from types import SimpleNamespace
    store = _store(tmp_path)
    for i in range(205):
        _insert(store, i, state="RUNNING")
    calls = []
    done = Event()

    def reconcile(*, project_id, lifecycle_id):
        current = store.get_agent_lifecycle(lifecycle_id)
        calls.append(lifecycle_id)
        if len(calls) == 205:
            done.set()
        return current.model_copy(update={"state": "GOAL_SATISFIED"})

    reconciler = SimpleNamespace(
        orchestrator=SimpleNamespace(get=lambda **kw: store.get_agent_lifecycle(kw["lifecycle_id"])),
        reconcile_once=reconcile,
    )
    coordinator = AgentExecutionCoordinator(store, reconciler=reconciler)
    try:
        assert len(coordinator.recover_on_startup()) == 100
        assert done.wait(15), len(calls)
    finally:
        assert coordinator.shutdown()
    assert len(set(calls)) == 205
    # The fixture does not persist a terminal state. Consumed wakes must still
    # never be recreated by a second scan of the same original run.
    for _ in range(3):
        store.scan_agent_recovery_page(consumer="execution", now=datetime.now(UTC))
    assert store.list_agent_execution_wakes(project_id="project-1") == []
    assert store.list_execution_tickets("project-1") == []


@pytest.mark.parametrize("limit", [0, 101])
def test_scan_rejects_unbounded_page_size(tmp_path, limit):
    with pytest.raises(ValueError, match="AGENT_RECOVERY_SCAN_INVALID"):
        _store(tmp_path).scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC), limit=limit)


def test_unsupported_persisted_contract_cannot_block_current_recovery(tmp_path):
    import json
    from src.backend.app.core.exceptions import SafetyError
    store = _store(tmp_path)
    old = _insert(store, 0)
    _insert(store, 1)
    payload = old.model_dump(mode="json")
    payload["schema_version"] = 5
    raw = json.dumps(payload)
    with store._connect() as conn:
        conn.execute("UPDATE agent_lifecycles SET payload=? WHERE lifecycle_id=?", (raw, old.lifecycle_id))
    page = store.scan_agent_recovery_page(consumer="planning", now=datetime.now(UTC))
    assert page.lifecycle_ids == ("scan-0001",)
    with pytest.raises(SafetyError, match="AGENT_LIFECYCLE_VERSION_UNSUPPORTED"):
        store.get_agent_lifecycle(old.lifecycle_id)
    with store._connect() as conn:
        assert conn.execute("SELECT payload FROM agent_lifecycles WHERE lifecycle_id=?", (old.lifecycle_id,)).fetchone()[0] == raw
    assert {wake.lifecycle_id for wake in store.list_agent_task_wakes(project_id="project-1")} == {"scan-0001"}
    assert store.list_execution_tickets("project-1") == []
