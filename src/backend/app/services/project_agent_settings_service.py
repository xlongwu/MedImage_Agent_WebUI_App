"""Project-scoped Agent defaults; preferences never grant execution authority."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.backend.app.core.exceptions import NotFoundError, SafetyError
from src.backend.app.schemas.project_agent_settings import (
    ProjectAgentSettings,
    RegisteredScientificResource,
    ScientificResourceInput,
    UpdateProjectAgentSettingsRequest,
)


class ProjectAgentSettingsService:
    def __init__(self, store) -> None:
        self.store = store

    def get(self, *, project_id: str) -> ProjectAgentSettings:
        project = self.store.get_project(project_id)
        if project is None:
            raise NotFoundError("PROJECT_NOT_FOUND", code="PROJECT_NOT_FOUND")
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        payload = metadata.get("agent_defaults")
        if not isinstance(payload, dict):
            payload = {}
        return ProjectAgentSettings(
            project_id=project_id,
            default_atlas=self._stored_resource(payload.get("default_atlas")),
            default_template=self._stored_resource(payload.get("default_template")),
            cpu_policy=str(payload.get("cpu_policy") or "auto"),
            compute_policy=str(payload.get("compute_policy") or "auto"),
        )

    def update(
        self,
        *,
        project_id: str,
        request: UpdateProjectAgentSettingsRequest,
    ) -> ProjectAgentSettings:
        project = self.store.get_project(project_id)
        if project is None:
            raise NotFoundError("PROJECT_NOT_FOUND", code="PROJECT_NOT_FOUND")
        metadata = project.metadata if isinstance(project.metadata, dict) else {}
        project_dir = Path(str(metadata.get("project_dir") or "")).expanduser().resolve()
        if not project_dir.is_dir():
            raise SafetyError("PROJECT_DIRECTORY_INVALID", code="PROJECT_DIRECTORY_INVALID")
        payload = {
            "schema_version": 1,
            "default_atlas": self._verify_resource(
                project_dir=project_dir, value=request.default_atlas, kind="atlas"
            ),
            "default_template": self._verify_resource(
                project_dir=project_dir, value=request.default_template, kind="template"
            ),
            "cpu_policy": request.cpu_policy,
            "compute_policy": request.compute_policy,
        }
        self.store.update_project_metadata(project_id, {"agent_defaults": payload})
        return self.get(project_id=project_id)

    @staticmethod
    def _stored_resource(value) -> RegisteredScientificResource | None:
        return RegisteredScientificResource.model_validate(value) if isinstance(value, dict) else None

    @staticmethod
    def _verify_resource(
        *, project_dir: Path, value: ScientificResourceInput | None, kind: str
    ) -> dict[str, str] | None:
        if value is None:
            return None
        name = value.name.strip()
        license_name = value.license.strip()
        if not name or not license_name:
            raise SafetyError(
                f"AGENT_{kind.upper()}_RESOURCE_INVALID",
                code=f"AGENT_{kind.upper()}_RESOURCE_INVALID",
            )
        resource_root = (project_dir / "resources").resolve()
        resolved = Path(value.path).expanduser().resolve()
        if (
            not resolved.is_file()
            or not resolved.is_relative_to(resource_root)
            or not (resolved.name.endswith(".nii") or resolved.name.endswith(".nii.gz"))
        ):
            raise SafetyError(
                f"AGENT_{kind.upper()}_RESOURCE_INVALID",
                code=f"AGENT_{kind.upper()}_RESOURCE_INVALID",
            )
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "name": name,
            "path": str(resolved),
            "license": license_name,
            "checksum": f"sha256:{digest.hexdigest()}",
        }
