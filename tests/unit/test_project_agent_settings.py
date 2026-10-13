from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.project_context import _verified_project_resource
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import (
    REQUIRED_MULTI_AGENT_DATA_CATEGORIES,
    ProjectMultiAgentConsent,
    ScientificResourceInput,
    UpdateProjectAgentSettingsRequest,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService


def _store(tmp_path: Path) -> SQLiteDesktopStore:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    store = SQLiteDesktopStore(tmp_path / "settings.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1", name="Project", study_id="study", modality="rs-fMRI",
            created_date="2026-08-23", subjects_count=1, current_pipeline_id="",
            sequences=[], scans_count=1, total_size="0", current_model_id="",
            metadata={"project_dir": str(project_dir)},
        ),
        health_status="ready",
        rawdata_dir="",
    )
    return store


def test_project_agent_settings_register_resources_and_preserve_auto_defaults(tmp_path: Path) -> None:
    store = _store(tmp_path)
    atlas = tmp_path / "project" / "resources" / "atlases" / "atlas.nii.gz"
    atlas.parent.mkdir(parents=True)
    atlas.write_bytes(b"registered-atlas")
    service = ProjectAgentSettingsService(store)

    updated = service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(
            default_atlas=ScientificResourceInput(name="Test atlas", path=str(atlas), license="CC-BY-4.0", space="MNI152"),
        ),
    )

    assert updated.cpu_policy == "auto"
    assert updated.compute_policy == "auto"
    assert updated.default_atlas is not None
    assert updated.default_atlas.checksum.startswith("sha256:")
    assert SQLiteDesktopStore(store.db_path).get_project("project-1").metadata["agent_defaults"]["default_atlas"]["path"] == str(atlas.resolve())


def test_project_agent_settings_reject_resource_outside_project_resources(tmp_path: Path) -> None:
    store = _store(tmp_path)
    outside = tmp_path / "atlas.nii.gz"
    outside.write_bytes(b"outside")

    with pytest.raises(SafetyError, match="AGENT_ATLAS_RESOURCE_INVALID"):
        ProjectAgentSettingsService(store).update(
            project_id="project-1",
            request=UpdateProjectAgentSettingsRequest(
                default_atlas=ScientificResourceInput(name="Outside", path=str(outside), license="CC0", space="MNI152"),
            ),
        )


def test_project_agent_settings_reject_non_nifti_and_blank_license(tmp_path: Path) -> None:
    store = _store(tmp_path)
    resource = tmp_path / "project" / "resources" / "atlases" / "atlas.txt"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"not-a-nifti")
    service = ProjectAgentSettingsService(store)

    with pytest.raises(SafetyError, match="AGENT_ATLAS_RESOURCE_INVALID"):
        service.update(
            project_id="project-1",
            request=UpdateProjectAgentSettingsRequest(
                default_atlas=ScientificResourceInput(
                    name="Atlas", path=str(resource), license="CC0", space="MNI152"
                ),
            ),
        )

    resource = resource.with_suffix(".nii")
    resource.write_bytes(b"synthetic-nifti")
    with pytest.raises(SafetyError, match="AGENT_ATLAS_RESOURCE_INVALID"):
        service.update(
            project_id="project-1",
            request=UpdateProjectAgentSettingsRequest(
                default_atlas=ScientificResourceInput(
                    name="Atlas", path=str(resource), license="   ", space="MNI152"
                ),
            ),
        )


def test_registered_resource_is_ignored_after_checksum_drift(tmp_path: Path) -> None:
    store = _store(tmp_path)
    atlas = tmp_path / "project" / "resources" / "atlases" / "atlas.nii.gz"
    atlas.parent.mkdir(parents=True)
    atlas.write_bytes(b"registered-atlas")
    updated = ProjectAgentSettingsService(store).update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(
            default_atlas=ScientificResourceInput(
                name="Atlas", path=str(atlas), license="CC0", space="MNI152"
            ),
        ),
    )
    assert updated.default_atlas is not None
    atlas.write_bytes(b"changed-atlas")

    assert (
        _verified_project_resource(
            updated.default_atlas.model_dump(mode="json"), tmp_path / "project"
        )
        is None
    )


def test_multi_agent_consent_is_closed_by_default_and_persists_epochs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = ProjectAgentSettingsService(store)
    initial = service.get(project_id="project-1").multi_agent_consent
    assert initial.enabled is False
    assert initial.consent_epoch == 0
    assert initial.allowed_data_categories == ()

    enabled = service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(
            multi_agent_consent_enabled=True,
            multi_agent_max_model_calls=4,
            multi_agent_max_input_tokens=8192,
        ),
    ).multi_agent_consent
    assert enabled.enabled is True
    assert enabled.consent_epoch == 1
    assert enabled.max_model_calls == 4
    assert enabled.max_input_tokens == 8192
    assert enabled.allowed_data_categories == REQUIRED_MULTI_AGENT_DATA_CATEGORIES
    assert enabled.consent_hash != initial.consent_hash
    assert ProjectAgentSettingsService(
        SQLiteDesktopStore(store.db_path)
    ).get(project_id="project-1").multi_agent_consent == enabled

    resource_only_update = service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(),
    ).multi_agent_consent
    assert resource_only_update == enabled

    repeated = service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(
            multi_agent_consent_enabled=True,
            multi_agent_max_model_calls=4,
            multi_agent_max_input_tokens=8192,
        ),
    ).multi_agent_consent
    assert repeated.consent_epoch == enabled.consent_epoch
    assert repeated.consent_hash == enabled.consent_hash

    revoked = service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    ).multi_agent_consent
    assert revoked.enabled is False
    assert revoked.consent_epoch == enabled.consent_epoch + 1
    assert revoked.allowed_data_categories == ()
    assert revoked.consent_hash != enabled.consent_hash


def test_agent_settings_consent_update_rejects_a_stale_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    service = ProjectAgentSettingsService(store)
    stale = service.get(project_id="project-1").multi_agent_consent
    first = ProjectMultiAgentConsent.build(
        project_id="project-1",
        enabled=True,
        consent_epoch=1,
        max_model_calls=6,
        max_input_tokens=16384,
    )
    store.update_project_agent_settings(
        "project-1",
        {"multi_agent_consent": first.model_dump(mode="json")},
        expected_consent_hash=stale.consent_hash,
    )
    stale_write = ProjectMultiAgentConsent.build(
        project_id="project-1",
        enabled=False,
        consent_epoch=1,
    )
    with pytest.raises(RuntimeError, match="PROJECT_AGENT_SETTINGS_CONCURRENT_UPDATE"):
        store.update_project_agent_settings(
            "project-1",
            {"multi_agent_consent": stale_write.model_dump(mode="json")},
            expected_consent_hash=stale.consent_hash,
        )
