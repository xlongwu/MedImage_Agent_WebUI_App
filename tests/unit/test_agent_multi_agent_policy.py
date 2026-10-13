from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.config_schema import AgentMultiAgentConfig
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import UpdateProjectAgentSettingsRequest
from src.backend.app.services.agent_multi_agent_policy_service import AgentMultiAgentPolicyService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService


def _store(tmp_path: Path) -> SQLiteDesktopStore:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    store = SQLiteDesktopStore(tmp_path / "policy.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1",
            name="Project",
            study_id="study-1",
            modality="rs-fMRI",
            created_date="test",
            subjects_count=0,
            current_pipeline_id="",
            sequences=[],
            scans_count=0,
            total_size="0",
            current_model_id="",
            metadata={"project_dir": str(project_dir)},
        ),
        health_status="ready",
        rawdata_dir="",
    )
    return store


def _enabled_config(**updates) -> AgentMultiAgentConfig:
    return AgentMultiAgentConfig(enabled=True, **updates)


def test_disabled_global_gate_returns_before_reading_project_or_provider() -> None:
    class StoreThatMustNotBeRead:
        def get_project(self, project_id: str):
            raise AssertionError("project lookup must wait until the global gate is enabled")

    policy = AgentMultiAgentPolicyService(
        StoreThatMustNotBeRead(), AgentMultiAgentConfig(enabled=False)
    )
    with pytest.raises(SafetyError, match="MULTI_AGENT_DISABLED"):
        policy.verify_team_budget(
            project_id="project-1", max_model_calls=3, reserved_input_tokens=100
        )


def test_budget_preflight_requires_current_project_consent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    policy = AgentMultiAgentPolicyService(store, _enabled_config())
    with pytest.raises(SafetyError, match="MULTI_AGENT_PROJECT_CONSENT_REQUIRED"):
        policy.verify_team_budget(
            project_id="project-1", max_model_calls=3, reserved_input_tokens=100
        )

    enabled = ProjectAgentSettingsService(store).update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=True),
    ).multi_agent_consent
    assert policy.verify_team_budget(
        project_id="project-1", max_model_calls=6, reserved_input_tokens=16384
    ) == enabled
    with pytest.raises(SafetyError, match="MULTI_AGENT_BUDGET_EXCEEDED"):
        policy.verify_team_budget(
            project_id="project-1", max_model_calls=7, reserved_input_tokens=16384
        )
    with pytest.raises(SafetyError, match="MULTI_AGENT_BUDGET_EXCEEDED"):
        policy.verify_team_budget(
            project_id="project-1", max_model_calls=6, reserved_input_tokens=16385
        )


def test_provider_preflight_rejects_revoked_consent_epoch_and_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    settings = ProjectAgentSettingsService(store)
    policy = AgentMultiAgentPolicyService(store, _enabled_config())
    enabled = settings.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=True),
    ).multi_agent_consent
    assert policy.preflight_provider_call(
        project_id="project-1",
        expected_consent_epoch=enabled.consent_epoch,
        expected_consent_hash=enabled.consent_hash,
        reserved_input_tokens=1024,
    ) == enabled

    settings.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    )
    with pytest.raises(SafetyError, match="MULTI_AGENT_CONSENT_INVALIDATED"):
        policy.preflight_provider_call(
            project_id="project-1",
            expected_consent_epoch=enabled.consent_epoch,
            expected_consent_hash=enabled.consent_hash,
            reserved_input_tokens=1024,
        )
