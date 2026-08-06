from __future__ import annotations

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import HarnessContextBuilder


def _project(metadata: dict) -> ProjectDetail:
    return ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=1, current_pipeline_id="pipeline",
        sequences=[], scans_count=1, total_size="0", current_model_id="none", metadata=metadata,
    )


def _lifecycle(context: dict | None = None) -> AgentLifecycleRecord:
    return AgentLifecycleRecord(
        lifecycle_id="lifecycle-1", project_id="project-1", state="CREATED",
        goal_text="Create a plan", command_context=context or {},
    )


def test_context_is_allowlisted_redacted_and_stably_hashed() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}, "memory_context": {"token": "secret", "memory_ids": ["m1"]}})
    project = _project({"subject_count": 1, "api_key": "secret", "rawdata_dir": "C:/private/rawdata"})

    first = builder.build(lifecycle=lifecycle, project=project)
    second = builder.build(lifecycle=lifecycle, project=project)

    assert first.context_hash == second.context_hash
    rendered = str(first.allowed_fields_json)
    assert "secret" not in rendered
    assert "rawdata" not in rendered.casefold()
    assert first.allowed_fields_json["confirmed_answers"] == {"atlas": "aal"}


def test_context_truncates_to_published_32kib_limit() -> None:
    builder = HarnessContextBuilder()
    fields, omitted = builder._truncate(
        {"goal": "plan", "lifecycle_state": "CREATED", "memory": {"detail": "x" * (builder.MAX_BYTES + 1)}},
        [],
    )

    assert len(__import__("json").dumps(fields).encode()) <= builder.MAX_BYTES
    assert omitted == ["memory"]
