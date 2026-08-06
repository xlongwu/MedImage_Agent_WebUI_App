"""Resolve persisted project metadata and bind it to reviewed plans."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.backend.app.runtime.tool_catalog import build_tool_catalog
from src.backend.app.services.mock_store import mock_store


class ProjectContextError(ValueError):
    """Raised when a project context cannot be resolved safely."""


@dataclass(frozen=True)
class ProjectContext:
    project_id: str | None
    project_config_path: Path
    project_dir: Path | None
    rawdata_dir: Path | None
    dataset_index_path: Path | None
    source: str
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_config_path": str(self.project_config_path),
            "project_dir": str(self.project_dir) if self.project_dir else None,
            "rawdata_dir": str(self.rawdata_dir) if self.rawdata_dir else None,
            "dataset_index_path": (
                str(self.dataset_index_path) if self.dataset_index_path else None
            ),
            "source": self.source,
            "diagnostics": dict(self.diagnostics),
        }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _path(value: Any) -> Path | None:
    if not isinstance(value, str | Path) or not str(value).strip():
        return None
    return Path(value).expanduser().resolve()


def _same_path(first: Path | None, second: Path | None) -> bool:
    return first is not None and second is not None and first == second


def _is_example_config(path: Path) -> bool:
    try:
        path.relative_to(Path("examples").resolve())
        return True
    except ValueError:
        return False


def _iter_nifti_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and (path.name.endswith(".nii") or path.name.endswith(".nii.gz"))
    ]


def _bids_subjects_from_nifti_files(nifti_files: list[Path]) -> list[str]:
    subjects: set[str] = set()
    for path in nifti_files:
        for part in path.parent.parts:
            if part.startswith("sub-"):
                subjects.add(part)
                break
    return sorted(subjects)


def _augment_diagnostics_with_registered_outputs(
    diagnostics: dict[str, Any],
    metadata: dict[str, Any],
    project_dir: Path | None,
    rawdata_dir: Path | None,
) -> dict[str, Any]:
    enriched = dict(diagnostics)
    if project_dir is not None:
        enriched.setdefault("project_dir", str(project_dir))

    converted_root = _path(
        metadata.get("preprocessing_input_dir")
        or metadata.get("converted_bids_dir")
        or metadata.get("last_conversion_output_root")
    )
    if converted_root is None and project_dir is not None:
        converted_root = project_dir / "converted_bids"

    if converted_root is not None and converted_root.is_dir():
        nifti_files = _iter_nifti_files(converted_root)
        if nifti_files:
            subjects = _bids_subjects_from_nifti_files(nifti_files)
            bold_count = sum(1 for path in nifti_files if "_bold" in path.name)
            t1w_count = sum(1 for path in nifti_files if "_T1w" in path.name)
            enriched.update(
                {
                    "status": "CONVERTED_BIDS",
                    "converted_bids_available": True,
                    "converted_bids_dir": str(converted_root),
                    "preprocessing_input_dir": str(converted_root),
                    "nifti_file_count": len(nifti_files),
                    "nifti_files": len(nifti_files),
                    "bold_nifti_count": bold_count,
                    "t1w_nifti_count": t1w_count,
                    "subjects_total": len(subjects) or enriched.get("subjects_total", 0),
                    "subject_candidates": subjects or enriched.get("subject_candidates", []),
                }
            )

    # A created project may directly reference an already registered BIDS/NIfTI
    # dataset rather than a project-local conversion output.  This is still a
    # read-only input: expose it to planning so execution can select the
    # reviewed native preprocessing chain, but never treat it as a write root.
    if (
        not enriched.get("converted_bids_available")
        and rawdata_dir is not None
        and rawdata_dir.is_dir()
    ):
        nifti_files = _iter_nifti_files(rawdata_dir)
        bold_files = [path for path in nifti_files if "_bold" in path.name]
        if bold_files:
            subjects = _bids_subjects_from_nifti_files(nifti_files)
            enriched.update(
                {
                    "status": "BIDS",
                    "registered_bids_available": True,
                    "preprocessing_input_dir": str(rawdata_dir),
                    "nifti_file_count": len(nifti_files),
                    "nifti_files": len(nifti_files),
                    "bold_nifti_count": len(bold_files),
                    "t1w_nifti_count": sum(
                        1 for path in nifti_files if "_T1w" in path.name
                    ),
                    "subjects_total": len(subjects)
                    or enriched.get("subjects_total", 0),
                    "subject_candidates": subjects
                    or enriched.get("subject_candidates", []),
                }
            )

    for key in (
        "preprocessing_conversion_run_id",
        "preprocessing_input_registry_path",
        "preprocessing_input_source",
        "agent_conversion_run_id",
        "agent_conversion_execution_ready",
        "agent_conversion_output_root",
    ):
        value = metadata.get(key)
        if value:
            enriched[key] = value

    if (
        enriched.get("agent_conversion_execution_ready") is True
        and enriched.get("agent_conversion_run_id")
    ):
        enriched.setdefault("conversion_run_id", enriched["agent_conversion_run_id"])
        enriched.setdefault("converted_bids_dir", enriched.get("agent_conversion_output_root"))

    handoff = metadata.get("native_full_preproc_handoff")
    if isinstance(handoff, dict):
        enriched["native_full_preproc_handoff"] = dict(handoff)
        if handoff.get("conversion_run_id") and not enriched.get("preprocessing_conversion_run_id"):
            enriched["preprocessing_conversion_run_id"] = handoff["conversion_run_id"]

    # ACPC is intentionally restricted to an already registered T1w artifact.
    # Do not discover an arbitrary NIfTI by path: reviewed planning must bind a
    # stable registry identifier before it can request an output write.
    if project_dir is not None:
        try:
            from src.backend.app.services.preprocessing_artifact_registry import (
                REGISTRY_FILENAME,
                load_artifact_registry,
            )

            candidates: list[Path] = []
            for directory_name in ("data", "work", "derivatives"):
                directory = project_dir / directory_name
                if directory.is_dir():
                    candidates.extend(directory.rglob(REGISTRY_FILENAME))
            t1_artifact_ids: list[str] = []
            for registry_path in sorted({path.resolve() for path in candidates}):
                for artifact in load_artifact_registry(registry_path).get("artifacts", []):
                    if not isinstance(artifact, dict):
                        continue
                    if str(artifact.get("artifact_type") or "") not in {"converted_t1w", "t1w", "coregistered_t1w"}:
                        continue
                    artifact_id = str(artifact.get("artifact_id") or "")
                    if artifact_id and artifact_id not in t1_artifact_ids:
                        t1_artifact_ids.append(artifact_id)
            if t1_artifact_ids:
                enriched["registered_t1_artifact_ids"] = t1_artifact_ids
        except Exception:
            # Context discovery remains read-only and must not fail unrelated
            # planning if an older registry is malformed.
            pass

    return enriched


def load_project_context(
    project_id: str | None,
    project_config_path: str | None,
    store=None,
) -> ProjectContext:
    project_store = store or mock_store
    """Load project paths from config, supplemented and checked against the store."""
    stored_project = None
    stored_metadata: dict[str, Any] = {}

    if project_id:
        stored_project = project_store.get_project(project_id)
        if stored_project is None:
            raise ProjectContextError(f"PROJECT_NOT_FOUND: {project_id}")
        stored_metadata = _mapping(stored_project.metadata)

    stored_config_path = _path(stored_metadata.get("project_config_path"))
    supplied_config_path = _path(project_config_path)
    if supplied_config_path and stored_config_path and supplied_config_path != stored_config_path:
        raise ProjectContextError(
            "PROJECT_CONFIG_MISMATCH: supplied project_config_path does not match project metadata"
        )

    config_path = supplied_config_path or stored_config_path
    if config_path is None:
        raise ProjectContextError("PROJECT_CONFIG_REQUIRED: project_config_path is required")
    if not config_path.exists() or not config_path.is_file():
        raise ProjectContextError(
            f"PROJECT_CONFIG_INVALID: project config does not exist: {config_path}"
        )

    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectContextError(f"PROJECT_CONFIG_INVALID: {exc}") from exc
    if not isinstance(config, dict):
        raise ProjectContextError("PROJECT_CONFIG_INVALID: project config must be a mapping")

    project_section = _mapping(config.get("project"))
    data_section = _mapping(config.get("data"))
    config_metadata = _mapping(config.get("metadata"))
    config_project_id = project_section.get("project_id")
    if project_id and config_project_id and str(config_project_id) != project_id:
        raise ProjectContextError(
            "PROJECT_ID_MISMATCH: project config does not belong to the selected project"
        )

    resolved_project_id = project_id or (
        str(config_project_id) if config_project_id else None
    )
    if stored_project is None and resolved_project_id:
        stored_project = project_store.get_project(resolved_project_id)
        if stored_project is not None:
            stored_metadata = _mapping(stored_project.metadata)

    metadata_config_path = _path(stored_metadata.get("project_config_path"))
    if metadata_config_path and metadata_config_path != config_path:
        raise ProjectContextError(
            "PROJECT_CONFIG_MISMATCH: project config does not match persisted metadata"
        )

    project_dir = _path(
        stored_metadata.get("project_dir")
        or project_section.get("root_dir")
        or config_metadata.get("project_dir")
    )

    config_rawdata = _path(
        data_section.get("rawdata_dir") or config_metadata.get("rawdata_dir")
    )
    metadata_rawdata = _path(stored_metadata.get("rawdata_dir"))
    if config_rawdata and metadata_rawdata and config_rawdata != metadata_rawdata:
        raise ProjectContextError(
            "RAWDATA_CONTEXT_MISMATCH: config rawdata_dir does not match project metadata"
        )
    rawdata_dir = config_rawdata or metadata_rawdata

    config_dataset_index = _path(
        data_section.get("dataset_index")
        or config_metadata.get("dataset_index_path")
    )
    metadata_dataset_index = _path(stored_metadata.get("dataset_index_path"))
    if (
        config_dataset_index
        and metadata_dataset_index
        and config_dataset_index != metadata_dataset_index
    ):
        raise ProjectContextError(
            "DATASET_INDEX_CONTEXT_MISMATCH: config dataset index does not match project metadata"
        )
    dataset_index_path = config_dataset_index or metadata_dataset_index

    source = str(
        stored_metadata.get("source")
        or config_metadata.get("source")
        or (
            "created"
            if config_project_id and data_section.get("copy_mode") == "reference"
            else "example"
            if _is_example_config(config_path)
            else "config"
        )
    )
    diagnostics = _mapping(
        stored_metadata.get("diagnostics") or config_metadata.get("diagnostics")
    )
    diagnostics = _augment_diagnostics_with_registered_outputs(
        diagnostics,
        stored_metadata,
        project_dir,
        rawdata_dir,
    )

    if source == "created":
        if rawdata_dir is None or not rawdata_dir.exists() or not rawdata_dir.is_dir():
            raise ProjectContextError(
                "RAWDATA_CONTEXT_INVALID: created project rawdata_dir is missing or invalid"
            )
        if (
            dataset_index_path is None
            or not dataset_index_path.exists()
            or not dataset_index_path.is_file()
        ):
            raise ProjectContextError(
                "DATASET_INDEX_CONTEXT_INVALID: created project dataset index is missing or invalid"
            )

    return ProjectContext(
        project_id=resolved_project_id,
        project_config_path=config_path,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
        dataset_index_path=dataset_index_path,
        source=source,
        diagnostics=diagnostics,
    )


def _subject_level_node_ids() -> set[str]:
    return {
        item.id
        for item in build_tool_catalog()
        if item.parallel_level == "subject"
    }


def apply_project_context_to_plan(
    plan: dict[str, Any],
    context: ProjectContext,
) -> dict[str, Any]:
    """Return a reviewed-plan candidate with deterministic project paths injected."""
    nodes = plan.get("nodes", []) or []
    if context.source == "created" and any(
        isinstance(node, dict) and node.get("id") == "create_synthetic_bids"
        for node in nodes
    ):
        raise ProjectContextError(
            "SYNTHETIC_DATA_NOT_ALLOWED: created projects cannot use create_synthetic_bids"
        )

    enriched = deepcopy(plan)
    enriched["project_context"] = context.to_dict()
    subject_nodes = _subject_level_node_ids()

    for node in enriched.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        params = node.setdefault("params", {})
        if not isinstance(params, dict):
            continue

        node_id = str(node.get("id", ""))
        if node_id == "data_inspection":
            if context.rawdata_dir is not None:
                params["rawdata_dir"] = str(context.rawdata_dir)
            if context.dataset_index_path is not None:
                params["output_dir"] = str(context.dataset_index_path.parent)
        if node_id in {
            "native_dicom_conversion_execute",
            "native_preproc_full_dry_run",
            "native_preproc_full_execute",
            "native_auto_acpc_align",
        }:
            if context.project_id:
                params.setdefault("project_id", context.project_id)
            if context.project_dir is not None:
                params.setdefault("project_dir", str(context.project_dir))
            if context.rawdata_dir is not None and node_id == "native_dicom_conversion_execute":
                params.setdefault("rawdata_dir", str(context.rawdata_dir))
            conversion_run_id = str(
                context.diagnostics.get("preprocessing_conversion_run_id")
                or context.diagnostics.get("conversion_run_id")
                or ""
            )
            if conversion_run_id:
                params.setdefault("conversion_run_id", conversion_run_id)
            if node_id == "native_dicom_conversion_execute":
                output_dir = str(
                    context.diagnostics.get("agent_conversion_output_root")
                    or context.diagnostics.get("converted_bids_dir")
                    or ""
                )
                if output_dir:
                    params.setdefault("output_dir", output_dir)
            if node_id == "native_auto_acpc_align" and context.project_dir is not None:
                params.setdefault("output_root", str(context.project_dir / "derivatives"))

        is_subject_level = (
            node_id in subject_nodes or node.get("parallel_level") == "subject"
        )
        if context.dataset_index_path is not None and (
            is_subject_level or "dataset_index" in params
        ):
            params["dataset_index"] = str(context.dataset_index_path)

    return enriched


def validate_plan_project_context(
    plan: dict[str, Any],
    context: ProjectContext,
) -> list[str]:
    """Validate a reviewed plan without mutating the already-reviewed content."""
    if context.source != "created":
        return []

    errors: list[str] = []
    nodes = plan.get("nodes", []) or []
    if any(
        isinstance(node, dict) and node.get("id") == "create_synthetic_bids"
        for node in nodes
    ):
        errors.append(
            "SYNTHETIC_DATA_NOT_ALLOWED: created projects cannot use create_synthetic_bids"
        )

    summary = plan.get("project_context")
    if not isinstance(summary, dict):
        errors.append("PROJECT_CONTEXT_MISSING: reviewed plan has no project_context")
    else:
        if context.project_id and summary.get("project_id") != context.project_id:
            errors.append("PROJECT_ID_MISMATCH: reviewed plan project_id does not match")
        for key, expected in (
            ("project_config_path", context.project_config_path),
            ("rawdata_dir", context.rawdata_dir),
            ("dataset_index_path", context.dataset_index_path),
        ):
            if expected is not None and _path(summary.get(key)) != expected:
                errors.append(
                    f"{key.upper()}_MISMATCH: reviewed plan {key} does not match project context"
                )

    subject_nodes = _subject_level_node_ids()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id", ""))
        params = node.get("params", {}) or {}
        if not isinstance(params, dict):
            continue

        rawdata_value = _path(params.get("rawdata_dir"))
        if rawdata_value is not None and not _same_path(rawdata_value, context.rawdata_dir):
            errors.append(
                f"RAWDATA_DIR_MISMATCH: node '{node_id}' rawdata_dir does not match project context"
            )
        if node_id == "data_inspection" and not _same_path(
            rawdata_value, context.rawdata_dir
        ):
            errors.append(
                "RAWDATA_DIR_REQUIRED: data_inspection must use the project rawdata_dir"
            )

        dataset_value = _path(params.get("dataset_index"))
        if dataset_value is not None and not _same_path(
            dataset_value, context.dataset_index_path
        ):
            errors.append(
                f"DATASET_INDEX_MISMATCH: node '{node_id}' dataset_index does not match project context"
            )
        if (
            node_id in subject_nodes or node.get("parallel_level") == "subject"
        ) and not _same_path(dataset_value, context.dataset_index_path):
            errors.append(
                f"DATASET_INDEX_REQUIRED: subject-level node '{node_id}' must use the project dataset index"
            )

    return errors
