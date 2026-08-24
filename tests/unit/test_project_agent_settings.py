from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import (
    ScientificResourceInput,
    UpdateProjectAgentSettingsRequest,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService
from src.backend.app.planner.project_context import _verified_project_resource


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
            default_atlas=ScientificResourceInput(name="Test atlas", path=str(atlas), license="CC-BY-4.0"),
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
                default_atlas=ScientificResourceInput(name="Outside", path=str(outside), license="CC0"),
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
                    name="Atlas", path=str(resource), license="CC0"
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
                    name="Atlas", path=str(resource), license="   "
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
                name="Atlas", path=str(atlas), license="CC0"
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
