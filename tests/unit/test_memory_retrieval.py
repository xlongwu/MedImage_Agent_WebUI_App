from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.config_schema import MemoryConfig
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.memory_influence_guard import (
    MemoryInfluenceError,
    MemoryInfluenceGuard,
)
from src.backend.app.planner.reviewed_plan_store import reviewed_plan_identity
from src.backend.app.services.memory_consolidation_service import (
    MemoryConsolidationService,
)
from src.backend.app.services.memory_management_service import MemoryManagementService
from src.backend.app.services.memory_repository import MemoryRepository
from src.backend.app.services.memory_repository import MemoryRepositoryError
from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _setup(tmp_path: Path, *, budget: int = 16384):
    store = SQLiteDesktopStore(tmp_path / "desktop.sqlite")
    project_id = store.list_projects()[0].id
    store.set_memory_consent(
        project_id=project_id,
        command_id="phase-d-consent-command-0001",
        principal="desktop-local-user",
        generate_enabled=True,
        use_enabled=True,
    )
    config = MemoryConfig(
        enabled=True,
        generation_enabled=True,
        use_enabled=True,
        store_path=str(tmp_path / "memory.sqlite"),
        max_context_bytes=budget,
    )
    repository = MemoryRepository(config.store_path)
    manager = MemoryManagementService(
        project_store=store, memory_repository=repository, config=config
    )
    consolidator = MemoryConsolidationService(
        project_store=store, memory_repository=repository
    )
    retrieval = MemoryRetrievalService(
        repository=repository, project_store=store, config=config
    )
    return store, repository, manager, consolidator, retrieval, project_id


def _remember_scientific(manager, repository, consolidator, project_id: str):
    result = manager.remember(
        project_id=project_id,
        command_id="phase-d-remember-atlas-0001",
        principal="desktop-local-user",
        kind="project_decision",
        key="atlas",
        value={"decision_kind": "atlas", "value": "schaefer-200"},
        summary="Use the confirmed Schaefer atlas for connectivity.",
        impact_class="scientific",
    )
    candidate = repository.get_candidate(
        project_id=project_id, candidate_id=result["candidate_id"]
    )
    manager.review_candidate(
        project_id=project_id,
        candidate_id=candidate.candidate_id,
        command_id="phase-d-accept-atlas-0001",
        principal="desktop-local-user",
        accept=True,
        expected_candidate_version=candidate.candidate_version,
        candidate_hash=candidate.candidate_hash,
    )
    consolidator.consolidate_project(project_id=project_id)


def test_context_is_deterministic_bounded_and_scientific_values_are_advisory(
    tmp_path: Path,
) -> None:
    _store, _repository, manager, consolidator, retrieval, project_id = _setup(
        tmp_path
    )
    _remember_scientific(manager, _repository, consolidator, project_id)

    first = retrieval.build_context(
        project_id=project_id, goal="Compute atlas connectivity"
    )
    second = retrieval.build_context(
        project_id=project_id, goal="Compute atlas connectivity"
    )

    assert first == second
    assert first.planner_constraints == {}
    assert first.status == "enabled"
    assert first.used_bytes > 0
    assert first.context_hash == stable_hash(
        first.model_dump(mode="json", exclude={"context_hash"})
    )
    assert len(first.decision_suggestions) == 1
    suggestion = first.decision_suggestions[0]
    assert suggestion.decision_kind == "atlas"
    assert suggestion.typed_value["value"] == "schaefer-200"
    assert suggestion.advisory_only is True


def test_install_and_project_use_gates_fail_closed(tmp_path: Path) -> None:
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    store.set_memory_consent(
        project_id=project_id,
        command_id="phase-d-consent-disable-0002",
        principal="desktop-local-user",
        generate_enabled=True,
        use_enabled=False,
    )
    assert retrieval.retrieve(project_id=project_id, query="atlas").items == ()

    disabled = MemoryRetrievalService(
        repository=repository,
        project_store=store,
        config=retrieval.config.model_copy(update={"enabled": False}),
    )
    assert disabled.retrieve(project_id=project_id, query="atlas").items == ()


def test_enabled_memory_store_failure_blocks_instead_of_returning_empty_context(
    tmp_path: Path, monkeypatch
) -> None:
    _store, repository, _manager, _consolidator, retrieval, project_id = _setup(tmp_path)
    monkeypatch.setattr(
        repository,
        "health_check",
        lambda: {"ok": False, "error_code": "MEMORY_STORE_UNHEALTHY"},
    )

    with pytest.raises(MemoryRepositoryError) as error:
        retrieval.build_context(project_id=project_id, goal="plan")

    assert error.value.code == "MEMORY_STORE_UNHEALTHY"


def test_explicitly_disabled_memory_does_not_probe_an_unhealthy_store(
    tmp_path: Path, monkeypatch
) -> None:
    _store, repository, _manager, _consolidator, retrieval, project_id = _setup(tmp_path)
    disabled = MemoryRetrievalService(
        repository=repository,
        project_store=_store,
        config=retrieval.config.model_copy(update={"enabled": False}),
    )
    monkeypatch.setattr(
        repository,
        "health_check",
        lambda: (_ for _ in ()).throw(AssertionError("health must not be called")),
    )

    context = disabled.build_context(project_id=project_id, goal="plan")

    assert context.status == "disabled"
    assert context.evidence_refs == ()


def test_stale_authoritative_source_is_excluded(tmp_path: Path) -> None:
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    item = repository.list_items(project_id=project_id)[0]
    with repository.connect(immediate=True) as conn:
        conn.execute(
            "UPDATE memory_sources SET source_trust_class='authoritative_structured', source_type='observation', source_id='missing' WHERE memory_id=?",
            (item.memory_id,),
        )
    result = retrieval.retrieve(project_id=project_id, query="atlas")
    assert result.items == ()
    assert result.omitted_count == 1


def test_stale_preference_is_retained_only_with_a_surfaced_warning(tmp_path: Path) -> None:
    _store, repository, manager, _consolidator, retrieval, project_id = _setup(
        tmp_path
    )
    manager.remember(
        project_id=project_id,
        command_id="phase-d-preference-stale-0001",
        principal="desktop-local-user",
        kind="user_preference",
        key="language",
        value={"language": "zh-CN"},
        summary="Prefer Chinese reports.",
        impact_class="presentation",
    )
    item = repository.list_items(project_id=project_id)[0]
    with repository.connect(immediate=True) as conn:
        conn.execute(
            "UPDATE memory_sources SET source_trust_class='authoritative_structured', source_type='observation', source_id='missing' WHERE memory_id=?",
            (item.memory_id,),
        )

    context, warnings = retrieval.build_context_with_warnings(
        project_id=project_id, goal="report language"
    )

    assert warnings == (f"MEMORY_SOURCE_STALE:{item.memory_id}",)
    assert context.evidence_refs[0].provenance_warning == "source_stale"


def test_byte_budget_omits_lower_ranked_items(tmp_path: Path) -> None:
    _store, _repository, manager, _consolidator, retrieval, project_id = _setup(
        tmp_path, budget=1024
    )
    for index in range(3):
        manager.remember(
            project_id=project_id,
            command_id=f"phase-d-preference-{index:04d}",
            principal="desktop-local-user",
            kind="presentation_preference",
            key=f"report-{index}",
            value={"style": "x" * 500, "index": index},
            summary=f"Report presentation preference {index} " + "x" * 300,
            impact_class="presentation",
        )
    result = retrieval.retrieve(project_id=project_id, query="report preference")
    assert result.omitted_count >= 1
    assert len(result.items) < 3


def test_influence_guard_requires_current_task_confirmation(tmp_path: Path) -> None:
    _store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    context = retrieval.build_context(project_id=project_id, goal="atlas connectivity")
    plan = {
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "python-cpu",
                "params": {"atlas": "schaefer-200"},
            }
        ]
    }
    guard = MemoryInfluenceGuard()
    with pytest.raises(MemoryInfluenceError) as error:
        guard.validate(plan=plan, memory_context=context)
    assert error.value.code == "MEMORY_SCIENTIFIC_CONFIRMATION_REQUIRED"
    guard.validate(
        plan=plan,
        memory_context=context,
        science_answers={"atlas": "schaefer-200"},
    )


def test_reviewed_plan_identity_binds_memory_snapshot(tmp_path: Path) -> None:
    _store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    context = retrieval.build_context(project_id=project_id, goal="atlas connectivity")
    plan = {"schema_version": "1.0", "pipeline_id": "p", "nodes": []}
    semantics = {
        "schema_version": 1,
        "goal_text": "plan",
        "goal_kind": "plan_only",
        "scope": {"subject_ids": [], "session_ids": [], "include": [], "exclude": [], "completeness_required": True},
        "criteria": [],
        "minimum_capability_level": "metadata_only",
        "allowed_limitation_flags": [],
        "forbidden_limitation_flags": [],
        "evaluation_policy_version": "goal-evaluator-v1",
    }
    first = reviewed_plan_identity(project_id, plan, semantics, context)
    changed = context.model_copy(update={"omitted_count": context.omitted_count + 1})
    second = reviewed_plan_identity(project_id, plan, semantics, changed)
    assert first != second
