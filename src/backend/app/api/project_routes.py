from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from src.backend.app.api._errors import raise_api_error
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.api.models import ProjectCreateRequest, ProjectCreateResponse
from src.backend.app.core.exceptions import StateStoreError
from src.backend.app.runtime.desktop_config import (
    add_authorized_data_dir,
    add_recent_project,
    get_desktop_config,
    remove_recent_project,
    set_active_project,
)
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.funraw_t1raw_detector import detect_funraw_t1raw_layout
from src.backend.app.services.mock_store import mock_store
from src.backend.app.tools.data_inspector import inspect_dataset
from src.backend.app.tools.project_config_writer import write_project_config

router = APIRouter()

DEFAULT_PROJECTS_ROOT = Path("outputs/projects")
NEXT_ACTIONS = [
    "Choose a pipeline",
    "Review dataset diagnostics",
    "Configure MATLAB/SPM/DPABI if needed",
]
_DICOM_EXTENSIONS = {".dcm", ".ima"}

_POSIX_SYSTEM_TREES = ("/System", "/usr", "/bin", "/etc")
_POSIX_EXACT_DANGEROUS = ("/", "/home", "/Users")
_WINDOWS_SYSTEM_TREES = (
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
)


def _same_or_descendant(candidate: str, root: str, separator: str) -> bool:
    return candidate == root or candidate.startswith(f"{root}{separator}")


def _dangerous_text_path_reason(path_text: str) -> str | None:
    windows_path = ntpath.normcase(ntpath.normpath(path_text.replace("/", "\\")))
    windows_drive, windows_tail = ntpath.splitdrive(windows_path)

    if windows_drive and windows_tail in {"", "\\"}:
        return "drive or network-share root directories are not allowed"

    for system_root in _WINDOWS_SYSTEM_TREES:
        normalized_root = ntpath.normcase(ntpath.normpath(system_root))
        if _same_or_descendant(windows_path, normalized_root, "\\"):
            return f"Windows system directory is not allowed: {system_root}"

    windows_parts = [part for part in windows_tail.split("\\") if part]
    if windows_drive and windows_parts:
        first_part = windows_parts[0].casefold()
        if first_part in {"windows", "program files", "program files (x86)"}:
            return "Windows system directory is not allowed"
        if first_part == "users" and len(windows_parts) <= 2:
            return "a user home root directory is not allowed"

    posix_path = posixpath.normpath(path_text.replace("\\", "/"))
    if posix_path in _POSIX_EXACT_DANGEROUS:
        return f"system or home root directory is not allowed: {posix_path}"

    for system_root in _POSIX_SYSTEM_TREES:
        if _same_or_descendant(posix_path, system_root, "/"):
            return f"system directory is not allowed: {system_root}"

    posix_parts = [part for part in posix_path.split("/") if part]
    if posix_path.startswith("/") and len(posix_parts) == 2:
        if posix_parts[0] in {"home", "Users"}:
            return "a user home root directory is not allowed"

    return None


def _dangerous_path_reason(raw_path: str, resolved_path: Path) -> str | None:
    """Return a reason when a path is a system/root/home location."""
    for path_text in (raw_path.strip(), str(resolved_path)):
        reason = _dangerous_text_path_reason(path_text)
        if reason:
            return reason

    if resolved_path == Path(resolved_path.anchor):
        return "filesystem root directories are not allowed"

    try:
        if resolved_path == Path.home().resolve():
            return "the current user home root directory is not allowed"
    except OSError:
        pass

    return None


def _resolve_rawdata_dir(rawdata_dir: str) -> Path:
    cleaned = rawdata_dir.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="rawdata_dir is required.")

    rawdata_path = Path(cleaned).expanduser().resolve()
    reason = _dangerous_path_reason(cleaned, rawdata_path)
    if reason:
        raise HTTPException(status_code=400, detail=f"rawdata_dir rejected: {reason}")
    if not rawdata_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"rawdata_dir does not exist: {cleaned}",
        )
    if not rawdata_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"rawdata_dir is not a directory: {cleaned}",
        )
    return rawdata_path


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass

    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _resolve_project_dir(
    requested_project_dir: str | None,
    project_id: str,
    rawdata_path: Path,
) -> Path:
    cleaned = requested_project_dir.strip() if requested_project_dir else ""
    project_dir = (
        Path(cleaned).expanduser().resolve()
        if cleaned
        else (DEFAULT_PROJECTS_ROOT / project_id).resolve()
    )

    reason = _dangerous_path_reason(cleaned or str(project_dir), project_dir)
    if reason:
        raise HTTPException(status_code=400, detail=f"project_dir rejected: {reason}")
    if _paths_overlap(project_dir, rawdata_path):
        raise HTTPException(
            status_code=400,
            detail="project_dir must not be rawdata_dir, inside rawdata_dir, or contain rawdata_dir.",
        )
    if project_dir.exists() and not project_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"project_dir is not a directory: {project_dir}",
        )
    return project_dir


def _validate_managed_output_paths(project_dir: Path, rawdata_path: Path) -> None:
    managed_paths = (
        project_dir / "data",
        project_dir / "project_config.yaml",
    )
    for managed_path in managed_paths:
        resolved_path = managed_path.resolve()
        try:
            resolved_path.relative_to(project_dir)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Managed project path escapes project_dir: {managed_path}",
            ) from exc
        if _paths_overlap(resolved_path, rawdata_path):
            raise HTTPException(
                status_code=400,
                detail=f"Managed project path overlaps rawdata_dir: {managed_path}",
            )

    data_dir, config_path = managed_paths
    if data_dir.exists() and not data_dir.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Managed data path is not a directory: {data_dir}",
        )
    if config_path.exists() and not config_path.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"Managed project config path is not a file: {config_path}",
        )


def _make_project_id(project_name: str) -> str:
    normalized_name = unicodedata.normalize("NFKC", project_name).strip()
    ascii_name = (
        unicodedata.normalize("NFKD", normalized_name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:48] or "project"
    digest = hashlib.sha256(normalized_name.casefold().encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def _find_recent_project_by_name(project_name: str) -> dict[str, Any] | None:
    normalized_name = unicodedata.normalize("NFKC", project_name).strip().casefold()
    recent_projects = get_desktop_config(redacted=False).get("recent_projects", [])
    if not isinstance(recent_projects, list):
        return None

    for item in recent_projects:
        if not isinstance(item, dict):
            continue
        existing_name = unicodedata.normalize(
            "NFKC", str(item.get("project_name", ""))
        ).strip().casefold()
        if existing_name == normalized_name:
            return item
    return None


def _deduplicate(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


def _diagnostics_from_dataset_index(
    dataset_index_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    payload = json.loads(dataset_index_path.read_text(encoding="utf-8"))
    subjects = payload.get("subjects", [])
    if not isinstance(subjects, list):
        subjects = []

    statuses = [
        str(subject.get("status", "INCOMPLETE")).upper()
        for subject in subjects
        if isinstance(subject, dict)
    ]
    nifti_file_count = 0
    for subject in subjects:
        if not isinstance(subject, dict):
            continue
        sessions = subject.get("sessions", [])
        for session in sessions if isinstance(sessions, list) else []:
            if not isinstance(session, dict):
                continue
            anat = session.get("anat", {})
            if (
                isinstance(anat, dict)
                and anat.get("exists")
                and isinstance(anat.get("t1w"), str)
            ):
                nifti_file_count += 1
            func = session.get("func", [])
            for run in func if isinstance(func, list) else []:
                if (
                    isinstance(run, dict)
                    and run.get("exists")
                    and isinstance(run.get("bold"), str)
                ):
                    nifti_file_count += 1
    subjects_total = len(statuses)
    subjects_complete = statuses.count("COMPLETE")
    subjects_warning = sum(
        status in {"WARNING", "MISSING_T1W", "MISSING_BOLD"} for status in statuses
    )
    subjects_incomplete = subjects_total - subjects_complete - subjects_warning

    if subjects_total == 0:
        status = "EMPTY"
    elif subjects_incomplete:
        status = "INCOMPLETE"
    elif subjects_warning:
        status = "WARNING"
    else:
        status = "READY"

    warnings: list[str] = []
    missing_t1w = sum(status in {"MISSING_T1W", "INCOMPLETE"} for status in statuses)
    missing_bold = sum(status in {"MISSING_BOLD", "INCOMPLETE"} for status in statuses)
    if missing_t1w:
        warnings.append(f"{missing_t1w} subject(s) are missing T1w data.")
    if missing_bold:
        warnings.append(f"{missing_bold} subject(s) are missing BOLD data.")

    return (
        {
            "subjects_total": subjects_total,
            "subjects_complete": subjects_complete,
            "subjects_warning": subjects_warning,
            "subjects_incomplete": subjects_incomplete,
            "nifti_file_count": nifti_file_count,
            "status": status,
        },
        warnings,
    )


def _inspect_rawdata(
    rawdata_path: Path,
    data_dir: Path,
) -> tuple[dict[str, Any], Path | None, list[str]]:
    diagnostics: dict[str, Any] = {
        "subjects_total": 0,
        "subjects_complete": 0,
        "subjects_warning": 0,
        "subjects_incomplete": 0,
        "status": "INSPECTION_FAILED",
    }
    warnings: list[str] = []

    try:
        result = inspect_dataset(
            rawdata_dir=str(rawdata_path),
            output_dir=str(data_dir),
            read_nifti_metadata=True,
        )
    except Exception as exc:
        warnings.append(f"Dataset inspection failed: {exc}")
        return diagnostics, None, warnings

    warnings.extend(str(item) for item in result.get("warnings", []))
    if not result.get("ok"):
        warnings.append(f"Dataset inspection returned errors: {result.get('errors', [])}")
        return diagnostics, None, warnings

    dataset_index_path = next(
        (
            Path(output)
            for output in result.get("outputs", [])
            if str(output).endswith("dataset_index.json")
        ),
        data_dir / "dataset_index.json",
    )
    if not dataset_index_path.exists():
        warnings.append("Dataset inspection did not produce dataset_index.json.")
        return diagnostics, None, warnings

    try:
        diagnostics, diagnostic_warnings = _diagnostics_from_dataset_index(
            dataset_index_path
        )
    except Exception as exc:
        warnings.append(f"Failed to read dataset diagnostics: {exc}")
        return diagnostics, dataset_index_path, warnings

    warnings.extend(diagnostic_warnings)
    return diagnostics, dataset_index_path, warnings


def _is_dicom_inventory_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in _DICOM_EXTENSIONS:
        return True
    return not suffix and path.name.isdigit()


def _subject_hint_from_dicom_path(path: Path) -> str:
    for part in reversed(path.parts):
        normalized = part.lower().replace("_", "-")
        if normalized.startswith("sub-"):
            return normalized
        if normalized.startswith("subject-"):
            return "sub-" + normalized.removeprefix("subject-")
    parent = path.parent.name.strip().lower().replace("_", "-")
    return parent or "dicom-series"


def _inspect_dicom_inventory(rawdata_path: Path) -> dict[str, Any]:
    """Count DICOM-like files for project classification without reading pixels."""
    funraw = detect_funraw_t1raw_layout(rawdata_path)
    dicom_file_count = int(funraw.get("dicom_file_count") or 0)
    series_count = int(funraw.get("series_count") or 0)
    subject_ids = {str(item) for item in funraw.get("subject_ids", []) if item}
    series_dirs: set[str] = set()

    try:
        for child in rawdata_path.rglob("*"):
            if not child.is_file() or not _is_dicom_inventory_file(child):
                continue
            dicom_file_count += 0 if funraw.get("dicom_file_count") else 1
            series_dirs.add(str(child.parent.resolve()))
            subject_ids.add(_subject_hint_from_dicom_path(child))
    except (OSError, PermissionError):
        pass

    if not series_count:
        series_count = len(series_dirs)
    subject_candidates = sorted(subject_ids)
    return {
        "dicom_file_count": dicom_file_count,
        "dicom_files": dicom_file_count,
        "dicom_series_count": series_count,
        "dicom_series": series_count,
        "raw_dicom_candidate_subjects": len(subject_candidates),
        "dicom_subject_count": len(subject_candidates),
        "subject_candidates": subject_candidates,
        "raw_dicom_layout": funraw.get("layout_type") or "generic_dicom",
    }


def _merge_dicom_inventory(
    diagnostics: dict[str, Any],
    inventory: dict[str, Any],
) -> bool:
    dicom_file_count = int(inventory.get("dicom_file_count") or 0)
    if dicom_file_count <= 0:
        return False

    candidate_count = int(inventory.get("raw_dicom_candidate_subjects") or 0)
    diagnostics.update(inventory)
    if int(diagnostics.get("subjects_total") or 0) <= 0 and candidate_count > 0:
        diagnostics["subjects_total"] = candidate_count
        diagnostics["subjects_complete"] = 0
        diagnostics["subjects_warning"] = candidate_count
        diagnostics["subjects_incomplete"] = 0
    if int(diagnostics.get("nifti_file_count") or diagnostics.get("nifti_files") or 0) <= 0:
        diagnostics["status"] = "RAW_DICOM"
    return True


def _dashboard_dataset_profile(
    dataset_index_path: str | None,
) -> tuple[list[str], int]:
    if not dataset_index_path:
        return [], 0

    try:
        payload = json.loads(Path(dataset_index_path).read_text(encoding="utf-8"))
    except Exception:
        return [], 0

    sequences: set[str] = set()
    scans_count = 0
    subjects = payload.get("subjects", [])
    for subject in subjects if isinstance(subjects, list) else []:
        if not isinstance(subject, dict):
            continue
        sessions = subject.get("sessions", [])
        for session in sessions if isinstance(sessions, list) else []:
            if not isinstance(session, dict):
                continue
            anat = session.get("anat", {})
            if isinstance(anat, dict) and anat.get("exists"):
                sequences.add("T1")
                scans_count += 1
            func = session.get("func", [])
            if isinstance(func, list) and func:
                sequences.add("BOLD")
                scans_count += len(func)

    return [name for name in ("T1", "BOLD") if name in sequences], scans_count


def _dashboard_project_from_create_response(
    response: ProjectCreateResponse,
    existing: ProjectDetail | None = None,
) -> ProjectDetail:
    now = datetime.now(UTC).replace(microsecond=0).isoformat()
    existing_created_at = (
        existing.metadata.get("created_at")
        if existing and isinstance(existing.metadata, dict)
        else None
    )
    created_at = str(existing_created_at or now)
    sequences, scans_count = _dashboard_dataset_profile(response.dataset_index_path)
    diagnostics = dict(response.diagnostics)
    if diagnostics.get("dicom_file_count") and not sequences:
        sequences = ["DICOM"]
        scans_count = int(diagnostics.get("dicom_file_count") or 0)

    return ProjectDetail(
        id=response.project_id,
        name=response.project_name,
        study_id=response.project_id,
        modality="MRI / DICOM" if diagnostics.get("dicom_file_count") else "rs-fMRI",
        created_date=created_at[:10],
        subjects_count=int(diagnostics.get("subjects_total", 0) or 0),
        current_pipeline_id="not-selected",
        sequences=sequences,
        scans_count=scans_count,
        total_size="Referenced rawdata",
        current_model_id="not-selected",
        metadata={
            "source": "created",
            "project_dir": response.project_dir,
            "rawdata_dir": response.rawdata_dir,
            "project_config_path": response.project_config_path,
            "dataset_index_path": response.dataset_index_path,
            "diagnostics": diagnostics,
            "created_at": created_at,
            "updated_at": now,
        },
    )


@router.post("/api/projects/create", response_model=ProjectCreateResponse)
def create_project(
    request: ProjectCreateRequest,
    store: ProjectStore = Depends(get_project_store),
) -> ProjectCreateResponse:
    """Create a project referencing a local BIDS-like rawdata directory."""
    project_name = request.project_name.strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="project_name is required.")

    rawdata_path = _resolve_rawdata_dir(request.rawdata_dir)
    project_id = _make_project_id(project_name)
    project_dir = _resolve_project_dir(request.project_dir, project_id, rawdata_path)
    dashboard_store_warnings: list[str] = []
    try:
        existing_dashboard_project = store.get_project(project_id)
    except Exception as exc:
        existing_dashboard_project = None
        dashboard_store_warnings.append(
            f"Dashboard project store preflight warning: {exc}"
        )
    if existing_dashboard_project and not request.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"A dashboard project with id '{project_id}' already exists. Set overwrite=true to update it.",
        )

    duplicate_project = _find_recent_project_by_name(project_name)
    if duplicate_project and not request.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"A project named '{project_name}' already exists. Set overwrite=true to regenerate it.",
        )
    if project_dir.exists() and not request.overwrite:
        raise HTTPException(
            status_code=409,
            detail=f"Project directory already exists: {project_dir}. Set overwrite=true to regenerate managed files.",
        )

    _validate_managed_output_paths(project_dir, rawdata_path)
    project_dir.mkdir(parents=True, exist_ok=True)
    data_dir = project_dir / "data"
    intended_dataset_index_path = data_dir / "dataset_index.json"

    warnings: list[str] = list(dashboard_store_warnings)
    if not (rawdata_path / "dataset_description.json").is_file():
        warnings.append(
            "dataset_description.json is missing; rawdata_dir may not be standard BIDS."
        )
    try:
        has_subject_dirs = any(
            child.is_dir() and child.name.startswith("sub-")
            for child in rawdata_path.iterdir()
        )
    except OSError as exc:
        has_subject_dirs = False
        warnings.append(f"Could not enumerate rawdata_dir: {exc}")
    if not has_subject_dirs:
        warnings.append(
            "No sub-* subject directories were found; rawdata_dir may not be valid BIDS."
        )

    if request.run_inspection:
        diagnostics, dataset_index_path, inspection_warnings = _inspect_rawdata(
            rawdata_path,
            data_dir,
        )
        warnings.extend(inspection_warnings)
    else:
        diagnostics = {
            "subjects_total": 0,
            "subjects_complete": 0,
            "subjects_warning": 0,
            "subjects_incomplete": 0,
            "status": "NOT_INSPECTED",
        }
        dataset_index_path = None
        warnings.append("Dataset inspection was skipped.")

    dicom_inventory = _inspect_dicom_inventory(rawdata_path)
    if _merge_dicom_inventory(diagnostics, dicom_inventory):
        warnings.append(
            "DICOM files were detected. Project was added as raw DICOM for conversion planning."
        )

    if diagnostics["status"] == "READY" and warnings:
        diagnostics["status"] = "WARNING"

    desktop_settings = get_desktop_config(redacted=False)
    try:
        project_config_path = write_project_config(
            project_id=project_id,
            project_name=project_name,
            project_dir=project_dir,
            rawdata_dir=rawdata_path,
            dataset_index_path=intended_dataset_index_path,
            spm_dir=str(desktop_settings.get("spm_dir") or "./third_party/spm12"),
            dpabi_dir=str(
                desktop_settings.get("dpabi_dir")
                or "./third_party/DPABI_V8.2_240510"
            ),
            matlab_command=str(desktop_settings.get("matlab_command") or "matlab"),
        )
    except Exception as exc:
        raise_api_error(
            exc,
            error_cls=StateStoreError,
            message=f"Failed to write project_config.yaml: {exc}",
        )

    try:
        add_recent_project(project_id, project_name, str(project_dir))
        set_active_project(project_id, str(project_dir))
        add_authorized_data_dir(str(rawdata_path))
    except Exception as exc:
        warnings.append(f"Desktop config update warning: {exc}")

    response = ProjectCreateResponse(
        ok=True,
        project_id=project_id,
        project_name=project_name,
        project_dir=str(project_dir),
        rawdata_dir=str(rawdata_path),
        project_config_path=str(project_config_path),
        dataset_index_path=str(dataset_index_path) if dataset_index_path else None,
        diagnostics=diagnostics,
        warnings=_deduplicate(warnings),
        next_actions=list(NEXT_ACTIONS),
    )
    dashboard_project = _dashboard_project_from_create_response(
        response,
        existing_dashboard_project,
    )
    try:
        dataset_type = (
            "dicom"
            if int(response.diagnostics.get("dicom_file_count") or 0) > 0
            and int(response.diagnostics.get("nifti_file_count") or response.diagnostics.get("nifti_files") or 0) == 0
            else "bids"
        )
        store.add_project(
            dashboard_project,
            health_status=str(response.diagnostics.get("status", "UNKNOWN")),
            rawdata_dir=response.rawdata_dir,
            dataset_type=dataset_type,
            overwrite=request.overwrite,
        )
    except ValueError as exc:
        if not request.overwrite:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        warnings.append(f"Dashboard project store update warning: {exc}")
    except Exception as exc:
        warnings.append(f"Dashboard project store update warning: {exc}")

    return response.model_copy(update={"warnings": _deduplicate(warnings)})


@router.delete("/api/projects/{project_id}")
def delete_project(
    project_id: str,
    store: ProjectStore = Depends(get_project_store),
) -> dict[str, Any]:
    """Remove a project from desktop dashboard indexes, leaving rawdata/project files intact."""
    if not store.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    removed_from_store = store.remove_project(project_id)
    try:
        removed_from_recent = remove_recent_project(project_id)
    except Exception as exc:
        return {
            "ok": True,
            "project_id": project_id,
            "removed_from_store": removed_from_store,
            "removed_from_recent": False,
            "deleted_files": False,
            "warning": f"Desktop recent-project cleanup warning: {exc}",
            "message": "Project was removed from the dashboard. Rawdata and project files were not deleted.",
        }
    return {
        "ok": True,
        "project_id": project_id,
        "removed_from_store": removed_from_store,
        "removed_from_recent": removed_from_recent,
        "deleted_files": False,
        "message": "Project was removed from the dashboard. Rawdata and project files were not deleted.",
    }
