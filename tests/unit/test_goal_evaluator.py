from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backend.app.planner.goal_contract_builder import (
    build_goal_contract_semantics,
    finalize_goal_contract,
)
from src.backend.app.planner.plan_validator import validate_goal_contract_reachability
from src.backend.app.planner.reviewed_plan_store import reviewed_plan_identity
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.desktop import ReviewedPlanRecord
from src.backend.app.schemas.goal_contract import GoalCriterion
from src.backend.app.schemas.observation import (
    ArtifactObservation,
    CapabilityObservation,
    NodeObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    ObservationSourceRef,
    PipelineObservation,
    ScientificObservation,
    ValidationObservation,
)
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.goal_evaluator import (
    GoalEvaluator,
    calculate_goal_evaluation_hash,
    evaluate_criterion,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.observation_collector import calculate_observation_hash


def _plan(subjects=("sub-01",)):
    return {
        "pipeline_id": "fc-plan",
        "nodes": [
            {
                "id": "functional_connectivity_subject",
                "backend": "python",
                "params": {"roi_count": 4},
            }
        ],
        "metadata": {"subject_ids": list(subjects)},
    }


def _contract(plan=None, goal="Compute atlas-grounded FC for every reviewed subject"):
    plan = plan or _plan()
    built = build_goal_contract_semantics(plan, goal)
    assert built.ok and built.semantics is not None
    reviewed_plan_id, plan_hash = reviewed_plan_identity("project-1", plan, built.semantics)
    contract = finalize_goal_contract(
        semantics=built.semantics,
        project_id="project-1",
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
    )
    return plan, contract


def _observation(contract, *, include_fc=True, artifact_source_ok=True, subjects=("sub-01",)):
    now = datetime.now(UTC)
    sources = [
        ObservationSourceRef(
            source_id="source-summary",
            source_type="pipeline_summary",
            read_status="ok",
            observed_at=now,
            freshness="fresh",
        ),
        ObservationSourceRef(
            source_id="source-nodes",
            source_type="node_state",
            read_status="ok",
            observed_at=now,
            freshness="fresh",
        ),
        ObservationSourceRef(
            source_id="source-artifacts",
            source_type="artifact_discovery",
            read_status="ok" if artifact_source_ok else "missing",
            observed_at=now,
            freshness="fresh",
        ),
        ObservationSourceRef(
            source_id="source-registry",
            source_type="artifact_registry",
            read_status="ok" if artifact_source_ok else "missing",
            observed_at=now,
            freshness="fresh",
        ),
        ObservationSourceRef(
            source_id="source-validation",
            source_type="validation",
            read_status="ok" if artifact_source_ok else "missing",
            observed_at=now,
            freshness="fresh",
        ),
        ObservationSourceRef(
            source_id="source-contract",
            source_type="node_contract",
            read_status="ok",
            observed_at=now,
            freshness="fresh",
        ),
    ]
    artifacts = (
        tuple(
            ArtifactObservation(
                artifact_id=f"fc-{subject}",
                artifact_type="fc_matrix",
                owner_node_id="functional_connectivity_subject",
                subject_id=subject,
                relative_path=f"derivatives/{subject}/fc.npy",
                exists=True,
                size_bytes=64,
                checksum_sha256="a" * 64,
                shape=(4, 4),
                dtype="float32",
                reload_status="passed",
                provenance_id=f"prov-{subject}",
                registration_status="registered",
                evidence_ids=("source-artifacts", "source-registry"),
            )
            for subject in subjects
        )
        if include_fc
        else ()
    )
    nodes = tuple(
        NodeObservation(
            node_id="functional_connectivity_subject",
            subject_id=subject,
            status="SUCCESS",
            backend="python",
            contract_version="1.0.0",
            evidence_ids=("source-nodes",),
        )
        for subject in subjects
    )
    record = ObservationRecord(
        observation_id="observation-1",
        bindings=ObservationBindings(
            project_id="project-1",
            lifecycle_id="lifecycle-1",
            reviewed_plan_id=contract.reviewed_plan_id,
            plan_hash=contract.plan_hash,
            goal_contract_id=contract.goal_contract_id,
            goal_contract_hash=contract.goal_contract_hash,
            run_id="run-1",
            execution_ticket_id="ticket-1",
            dispatch_id="dispatch-1",
        ),
        collected_at=now,
        sources=tuple(sources),
        pipeline=PipelineObservation(
            status="SUCCESS",
            nodes_total=len(nodes),
            nodes_succeeded=len(nodes),
            nodes_failed=0,
            active_nodes=0,
            summary_consistent=True,
            evidence_ids=("source-summary",),
        ),
        nodes=nodes,
        artifacts=artifacts,
        validations=(
            ValidationObservation(
                validation_id="validation-1",
                validator_id="fc-integrity",
                validator_version="1",
                scope="run-1",
                status="passed",
                evidence_ids=("source-validation",),
            ),
        )
        if artifact_source_ok
        else (),
        capability=CapabilityObservation(
            declared_level="computed",
            observed_level="computed" if include_fc else "metadata_only",
            defensible_level="computed" if include_fc else "metadata_only",
            evidence_ids=("source-artifacts", "source-contract"),
        ),
        scientific=ScientificObservation(
            status="computed" if include_fc else "metadata_only",
            backend_ids=("python",),
            validation_evidence_ids=("source-validation",) if artifact_source_ok else (),
        ),
        completeness=ObservationCompleteness(
            status="complete" if artifact_source_ok else "partial",
            missing_sources=() if artifact_source_ok else ("artifacts", "validation"),
        ),
        observation_hash="pending",
    )
    return record.model_copy(update={"observation_hash": calculate_observation_hash(record)})


def _store_with(contract, observation, tmp_path):
    store = SQLiteDesktopStore(tmp_path / "goal-evaluation.sqlite")
    store.add_reviewed_plan(
        ReviewedPlanRecord(
            reviewed_plan_id=contract.reviewed_plan_id,
            project_id="project-1",
            project_config_path=str(tmp_path / "project.yaml"),
            plan_hash=contract.plan_hash,
            created_at="2026-07-14T00:00:00Z",
            updated_at="2026-07-14T00:00:00Z",
            payload={
                "plan": _plan(),
                "goal_contract": contract.model_dump(mode="json"),
                "goal_contract_status": "reviewed",
            },
        )
    )
    store.add_observation(observation)
    return store


def test_goal_contract_is_hash_bound_and_reachable():
    plan, contract = _contract()
    assert not validate_goal_contract_reachability(plan, contract)
    _, changed = _contract(goal="Compute a different reviewed FC goal")
    assert changed.goal_contract_hash != contract.goal_contract_hash
    assert changed.plan_hash != contract.plan_hash


@pytest.mark.parametrize(
    ("node_id", "expected_artifacts"),
    [
        ("alff_falff_subject", {"alff_map", "falff_map"}),
        ("reho_subject", {"reho_map"}),
        ("functional_connectivity_subject", {"fc_matrix"}),
        (
            "native_preproc_full_execute",
            {
                "residual_bold",
                "filtered_bold",
                "alff_map",
                "falff_map",
                "reho_map",
                "fc_matrix",
            },
        ),
    ],
)
def test_first_batch_scientific_goal_artifacts_are_contract_reachable(node_id, expected_artifacts):
    plan = {
        "pipeline_id": f"{node_id}-plan",
        "nodes": [
            {
                "id": node_id,
                "backend": "native_python" if node_id.startswith("native_") else "python",
                "params": {},
            }
        ],
    }
    built = build_goal_contract_semantics(plan, f"Review {node_id} outputs")
    assert built.ok and built.semantics is not None
    reviewed_plan_id, plan_hash = reviewed_plan_identity("project-1", plan, built.semantics)
    contract = finalize_goal_contract(
        semantics=built.semantics,
        project_id="project-1",
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
    )
    artifact_targets = {
        criterion.target
        for criterion in contract.criteria
        if criterion.criterion_type == "artifact_present"
    }
    assert artifact_targets == expected_artifacts
    assert not validate_goal_contract_reachability(plan, contract)


def test_native_minimal_goal_contract_uses_reviewed_scope_and_enabled_outputs():
    plan = {
        "pipeline_id": "native_full_preprocessing",
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "params": {
                    "subject_id": "sub-001",
                    "stage_overrides": {
                        "nuisance_regression": True,
                        "temporal_filtering": True,
                        "alff": False,
                        "falff": False,
                        "reho": False,
                        "functional_connectivity": False,
                    },
                },
            }
        ],
        "metadata": {
            "subject_scope": ["sub-001"],
            "required_preprocessing_stages": [
                "nuisance_regression",
                "temporal_filtering",
            ],
        },
    }

    built = build_goal_contract_semantics(
        plan,
        "Only preprocess sub-001 with the reviewed minimal rs-fMRI stage profile",
    )

    assert built.ok and built.semantics is not None
    assert built.semantics["scope"]["subject_ids"] == ["sub-001"]
    artifact_targets = {
        criterion["target"]
        for criterion in built.semantics["criteria"]
        if criterion["criterion_type"] == "artifact_present"
    }
    assert artifact_targets == {"residual_bold", "filtered_bold"}
    assert built.semantics["allowed_limitation_flags"] == ["simplified"]
    assert built.semantics["forbidden_limitation_flags"] == ["preview_only", "partial"]
    scope_criterion = next(
        criterion
        for criterion in built.semantics["criteria"]
        if criterion["criterion_type"] == "scope_complete"
    )
    assert set(scope_criterion["expected"]["artifact_types"]) == artifact_targets
    scientific_criterion = next(
        criterion
        for criterion in built.semantics["criteria"]
        if criterion["criterion_type"] == "scientific_status_allowed"
    )
    assert scientific_criterion["expected"]["forbidden_limitation_flags"] == [
        "preview_only",
        "partial",
    ]


def test_native_minimal_computed_outputs_allow_reviewed_simplified_limitation(tmp_path):
    plan = {
        "pipeline_id": "native_full_preprocessing",
        "nodes": [
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "params": {
                    "subject_id": "sub-001",
                    "stage_overrides": {
                        "nuisance_regression": True,
                        "temporal_filtering": True,
                        "group_summary": False,
                    },
                },
            }
        ],
    }
    _, contract = _contract(
        plan=plan,
        goal="Preprocess sub-001 with the reviewed native minimal profile",
    )
    observation = _observation(contract).model_copy(
        update={
            "nodes": (
                NodeObservation(
                    node_id="native_preproc_full_execute",
                    subject_id="sub-001",
                    status="SUCCESS",
                    backend="native_python",
                    contract_version="1.0.0",
                    evidence_ids=("source-nodes",),
                ),
            ),
            "artifacts": tuple(
                ArtifactObservation(
                    artifact_id=f"artifact-{artifact_type}",
                    artifact_type=artifact_type,
                    owner_node_id="native_preproc_full_execute",
                    subject_id="sub-001",
                    relative_path=f"derivatives/sub-001/{artifact_type}.nii.gz",
                    exists=True,
                    size_bytes=64,
                    checksum_sha256="a" * 64,
                    shape=(2, 2, 2, 4),
                    dtype="float32",
                    reload_status="passed",
                    provenance_id=f"provenance-{artifact_type}",
                    registration_status="registered",
                    evidence_ids=("source-artifacts", "source-registry"),
                )
                for artifact_type in ("residual_bold", "filtered_bold")
            ),
            "capability": CapabilityObservation(
                declared_level="computed",
                observed_level="computed",
                defensible_level="computed",
            ),
            "scientific": ScientificObservation(
                status="computed",
                limitation_flags=("simplified",),
                backend_ids=("native_python",),
            ),
        }
    )
    observation = observation.model_copy(
        update={"observation_hash": calculate_observation_hash(observation)}
    )

    store = _store_with(contract, observation, tmp_path)
    evaluation = GoalEvaluator(store).evaluate(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        observation_id=observation.observation_id,
    )

    assert evaluation.status == "satisfied"
    assert all(
        item.reason_code not in {"CAPABILITY_BELOW_MINIMUM", "SCIENTIFIC_STATUS_NOT_ALLOWED"}
        for item in evaluation.criterion_results
    )


def test_metadata_helper_does_not_lower_scientific_goal_reachability():
    plan = _plan()
    plan["nodes"].insert(
        0,
        {
            "id": "data_inspection",
            "backend": "python",
            "params": {},
        },
    )
    built = build_goal_contract_semantics(plan, "Compute reviewed FC")
    assert built.ok and built.semantics is not None
    reviewed_plan_id, plan_hash = reviewed_plan_identity("project-1", plan, built.semantics)
    contract = finalize_goal_contract(
        semantics=built.semantics,
        project_id="project-1",
        reviewed_plan_id=reviewed_plan_id,
        plan_hash=plan_hash,
    )
    assert not validate_goal_contract_reachability(plan, contract)


def test_fc_success_requires_real_artifact_and_is_persisted(tmp_path):
    _, contract = _contract()
    observation = _observation(contract)
    store = _store_with(contract, observation, tmp_path)
    evaluation = GoalEvaluator(store).evaluate(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        observation_id=observation.observation_id,
    )
    assert evaluation.status == "satisfied"
    assert all(result.status == "passed" for result in evaluation.criterion_results)
    assert calculate_goal_evaluation_hash(evaluation) == evaluation.goal_evaluation_hash
    assert (
        SQLiteDesktopStore(store.db_path).get_goal_evaluation(evaluation.goal_evaluation_id)
        == evaluation
    )


def test_runtime_success_with_missing_fc_is_not_satisfied(tmp_path):
    _, contract = _contract()
    observation = _observation(contract, include_fc=False)
    store = _store_with(contract, observation, tmp_path)
    evaluation = GoalEvaluator(store).evaluate(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        observation_id=observation.observation_id,
    )
    assert evaluation.status == "not_satisfied"
    assert any(result.reason_code == "ARTIFACT_MISSING" for result in evaluation.criterion_results)


def test_missing_artifact_source_is_indeterminate_not_false_missing(tmp_path):
    _, contract = _contract()
    observation = _observation(contract, include_fc=False, artifact_source_ok=False)
    store = _store_with(contract, observation, tmp_path)
    evaluation = GoalEvaluator(store).evaluate(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        observation_id=observation.observation_id,
    )
    assert evaluation.status == "indeterminate"
    assert any(
        result.reason_code == "ARTIFACT_SOURCE_INCOMPLETE"
        for result in evaluation.criterion_results
    )


def test_conflicting_observation_is_indeterminate_not_false_missing(tmp_path):
    _, contract = _contract()
    observation = _observation(contract, include_fc=False).model_copy(
        update={
            "completeness": ObservationCompleteness(
                status="invalid",
                conflicts=("ARTIFACT_REGISTRY_DISCOVERY_CONFLICT",),
            ),
            "observation_hash": "pending",
        }
    )
    observation = observation.model_copy(
        update={"observation_hash": calculate_observation_hash(observation)}
    )
    store = _store_with(contract, observation, tmp_path)
    evaluation = GoalEvaluator(store).evaluate(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        observation_id=observation.observation_id,
    )
    assert evaluation.status == "indeterminate"
    assert not any(
        result.reason_code == "ARTIFACT_MISSING" for result in evaluation.criterion_results
    )


@pytest.mark.parametrize(
    ("status", "limitation_flags", "expected"),
    [
        ("metadata_only", (), "failed"),
        ("computed", (), "passed"),
        ("validated", (), "passed"),
        ("computed", ("preview_only",), "failed"),
        ("computed", ("partial",), "failed"),
        ("computed", ("simplified",), "failed"),
    ],
)
def test_scientific_status_and_limitations_are_explicit(status, limitation_flags, expected):
    _, contract = _contract()
    observation = _observation(contract).model_copy(
        update={
            "scientific": ScientificObservation(
                status=status,
                limitation_flags=limitation_flags,
                backend_ids=("python",),
                validation_evidence_ids=("source-validation",),
            )
        }
    )
    criterion = next(
        item for item in contract.criteria if item.criterion_type == "scientific_status_allowed"
    )
    assert evaluate_criterion(criterion, contract, observation).status == expected


def test_all_any_count_and_fraction_quantifiers_are_deterministic():
    _, contract = _contract(plan=_plan(("sub-01", "sub-02", "sub-03")))
    observation = _observation(
        contract,
        subjects=("sub-01",),
    )
    base = next(item for item in contract.criteria if item.criterion_type == "artifact_present")
    cases = [
        ("all", None, None, "failed"),
        ("any", None, None, "passed"),
        ("at_least_count", 2, None, "failed"),
        ("at_least_fraction", None, 0.3, "passed"),
    ]
    for quantifier, count, fraction, expected in cases:
        criterion = GoalCriterion(
            **{
                **base.model_dump(mode="python"),
                "quantifier": quantifier,
                "threshold_count": count,
                "threshold_fraction": fraction,
            }
        )
        assert evaluate_criterion(criterion, contract, observation).status == expected


def test_lifecycle_reaches_goal_satisfied_only_after_persisted_evaluation(tmp_path):
    _, contract = _contract()
    observation = _observation(contract)
    store = _store_with(contract, observation, tmp_path)
    now = datetime.now(UTC)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="lifecycle-1",
        project_id="project-1",
        state="OBSERVING",
        reviewed_plan_id=contract.reviewed_plan_id,
        goal_contract_id=contract.goal_contract_id,
        goal_contract_hash=contract.goal_contract_hash,
        observation_id=observation.observation_id,
        observation_summary=observation.summary(),
        created_at=now,
        updated_at=now,
        last_command_id="created",
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-created",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            command_id="created",
            actor="test",
            source_command="fixture",
            occurred_at=now,
            from_state=None,
            to_state="OBSERVING",
            reviewed_plan_id=lifecycle.reviewed_plan_id,
            observation_id=lifecycle.observation_id,
            goal_contract_id=lifecycle.goal_contract_id,
        ),
    )
    completed, evaluation = AgentOrchestrator(store).evaluate_goal(
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        command_id="evaluate-1",
        actor="evaluator",
    )
    assert evaluation.status == "satisfied"
    assert completed.state == "GOAL_SATISFIED"
    assert completed.goal_evaluation_id == evaluation.goal_evaluation_id
