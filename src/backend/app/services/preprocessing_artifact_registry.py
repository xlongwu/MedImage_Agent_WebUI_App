"""Preprocessing artifact registry service.

The registry is a deterministic metadata ledger for preprocessing inputs and
stage outputs. It writes JSON atomically, references existing artifacts, and
never runs external tools or modifies rawdata.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.schemas.preprocessing_artifacts import (
    BidsEntitySet,
    PreprocessingArtifactRef,
    PreprocessingArtifactRegistry,
    PreprocessingArtifactRegistryWriteResult,
    PreprocessingInputInventory,
)
from src.backend.app.schemas.preprocessing_stage_catalog import (
    get_preprocessing_stage_spec,
)

REGISTRY_FILENAME = "preprocessing_artifact_registry.json"
_NIFTI_EXTS = (".nii", ".nii.gz")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_id(value: str, fallback_seed: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    if cleaned:
        return cleaned[:96]
    return hashlib.sha256(fallback_seed.encode("utf-8")).hexdigest()[:16]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _path_for_registry(path: Path, project_root: Path | None) -> tuple[str, str]:
    resolved = path.resolve()
    if project_root and _is_relative_to(resolved, project_root):
        return resolved.relative_to(project_root.resolve()).as_posix(), "project_relative"
    return str(resolved), "local_runtime"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inspect_artifact_payload(path: Path) -> tuple[list[int], str]:
    """Best-effort shape/dtype capture for reloadable scientific artifacts."""
    if not path.exists() or not path.is_file():
        return [], ""

    suffixes = "".join(path.suffixes).lower()
    try:
        if suffixes.endswith((".nii", ".nii.gz")):
            import nibabel as nib

            img = nib.load(str(path))
            return list(img.shape), str(img.get_data_dtype())
        if path.suffix.lower() == ".npy":
            import numpy as np

            arr = np.load(path, mmap_mode="r")
            return list(arr.shape), str(arr.dtype)
        if path.suffix.lower() in {".tsv", ".csv"}:
            delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                rows = [line.rstrip("\n").split(delimiter) for line in handle.readlines()]
            if rows:
                return [max(len(rows) - 1, 0), len(rows[0])], "table"
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            matrix = payload.get("matrix") if isinstance(payload, dict) else None
            if isinstance(matrix, list) and matrix:
                width = len(matrix[0]) if isinstance(matrix[0], list) else 1
                return [len(matrix), width], "json"
    except Exception:
        return [], ""
    return [], ""


def _extension_for(path: Path) -> str:
    suffixes = "".join(path.suffixes).lower()
    if suffixes.endswith(".nii.gz"):
        return ".nii.gz"
    return path.suffix.lower()


def parse_bids_entities(path: Path) -> BidsEntitySet:
    """Parse common BIDS entities from a file path and filename."""
    extension = _extension_for(path)
    name = path.name
    stem = name[: -len(extension)] if extension and name.endswith(extension) else path.stem
    tokens = stem.split("_")
    raw: dict[str, str] = {}
    suffix = ""
    for token in tokens:
        if "-" in token:
            key, value = token.split("-", 1)
            raw[key] = value
        elif token:
            suffix = token

    subject = raw.get("sub", "")
    session = raw.get("ses", "")
    datatype = ""
    for part in reversed(path.parts):
        if part in {"func", "anat", "fmap", "dwi", "perf"}:
            datatype = part
            break
        if part.startswith("sub-") and not subject:
            subject = part[4:]
        if part.startswith("ses-") and not session:
            session = part[4:]

    return BidsEntitySet(
        subject_id=f"sub-{subject}" if subject and not subject.startswith("sub-") else subject,
        session_id=f"ses-{session}" if session and not session.startswith("ses-") else session,
        task=raw.get("task", ""),
        run_id=f"run-{raw['run']}" if raw.get("run") else "",
        acquisition=f"acq-{raw['acq']}" if raw.get("acq") else "",
        direction=f"dir-{raw['dir']}" if raw.get("dir") else "",
        datatype=datatype,
        suffix=suffix,
        extension=extension,
        raw_entities=raw,
    )


def _artifact_type_for(path: Path) -> str:
    entities = parse_bids_entities(path)
    suffix = entities.suffix.lower()
    lower_name = path.name.lower()
    if path.suffix.lower() == ".json":
        return "sidecar_json"
    if suffix == "bold" or "bold" in lower_name or "rest" in lower_name:
        return "converted_bold"
    if suffix == "t1w" or "t1" in lower_name:
        return "converted_t1w"
    return "stage_manifest" if path.suffix.lower() == ".json" else "input_inventory"


def _artifact_id(
    artifact_type: str,
    path_value: str,
    *,
    stage_id: str,
    source_id: str,
) -> str:
    seed = f"{source_id}:{stage_id}:{artifact_type}:{path_value}"
    return f"ppart-{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _make_artifact_ref(
    path: Path,
    *,
    artifact_type: str,
    stage_id: str,
    project_root: Path | None,
    source_id: str,
    created_at: str,
    backend: str = "",
    source_artifact_ids: list[str] | None = None,
    provenance_path: str = "",
    qc_path: str = "",
    metadata: dict[str, Any] | None = None,
) -> PreprocessingArtifactRef:
    entities = parse_bids_entities(path)
    path_value, path_kind = _path_for_registry(path, project_root)
    checksum = _sha256_file(path) if path.exists() and path.is_file() else ""
    shape, dtype = _inspect_artifact_payload(path)
    return PreprocessingArtifactRef(
        artifact_id=_artifact_id(
            artifact_type,
            path_value,
            stage_id=stage_id,
            source_id=source_id,
        ),
        artifact_type=artifact_type,
        stage_id=stage_id,
        subject_id=entities.subject_id,
        session_id=entities.session_id,
        run_id=entities.run_id,
        path=path_value,
        path_kind=path_kind,
        shape=shape,
        dtype=dtype,
        suffix=entities.suffix,
        source_artifact_ids=source_artifact_ids or [],
        checksum=checksum,
        created_at=created_at,
        backend=backend,
        provenance_path=provenance_path,
        qc_path=qc_path,
        bids_entities=entities.model_dump(),
        metadata=metadata or {},
    )


def _discover_files(input_dir: Path) -> tuple[list[Path], list[Path], list[Path]]:
    bold_files: list[Path] = []
    t1w_files: list[Path] = []
    sidecar_files: list[Path] = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        ext = _extension_for(path)
        if ext in _NIFTI_EXTS:
            artifact_type = _artifact_type_for(path)
            if artifact_type == "converted_bold":
                bold_files.append(path)
            elif artifact_type == "converted_t1w":
                t1w_files.append(path)
        elif path.suffix.lower() == ".json":
            sidecar_files.append(path)
    return bold_files, t1w_files, sidecar_files


def _subjects(paths: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    for path in paths:
        subject = parse_bids_entities(path).subject_id
        if subject:
            seen.add(subject)
    return sorted(seen)


def _sessions(paths: Iterable[Path]) -> list[str]:
    seen: set[str] = set()
    for path in paths:
        session = parse_bids_entities(path).session_id
        if session:
            seen.add(session)
    return sorted(seen)


def _nifti_sidecar_key(path: Path) -> str:
    name = path.name
    ext = _extension_for(path)
    return name[: -len(ext)] if ext and name.endswith(ext) else path.stem


def _missing_sidecars(nifti_paths: list[Path], sidecars: list[Path]) -> list[dict[str, str]]:
    sidecar_keys = {_nifti_sidecar_key(path): path for path in sidecars}
    missing: list[dict[str, str]] = []
    for nifti in nifti_paths:
        key = _nifti_sidecar_key(nifti)
        if key not in sidecar_keys:
            missing.append({"nifti_path": str(nifti), "expected_sidecar_stem": key})
    return missing


def _inventory_from_artifacts(
    *,
    source_kind: str,
    conversion_run_id: str,
    input_root: Path,
    input_root_value: str,
    input_root_kind: str,
    bold_files: list[Path],
    t1w_files: list[Path],
    sidecars: list[Path],
    artifacts: list[PreprocessingArtifactRef],
    warnings: list[str],
) -> PreprocessingInputInventory:
    bold_subjects = set(_subjects(bold_files))
    t1w_subjects = set(_subjects(t1w_files))
    artifacts_by_type: dict[str, list[str]] = {}
    for artifact in artifacts:
        artifacts_by_type.setdefault(artifact.artifact_type, []).append(artifact.artifact_id)
    all_nifti = [*bold_files, *t1w_files]
    return PreprocessingInputInventory(
        source_kind=source_kind,
        conversion_run_id=conversion_run_id,
        input_root=input_root_value,
        input_root_path_kind=input_root_kind,
        subjects=sorted(bold_subjects | t1w_subjects),
        sessions=_sessions(all_nifti),
        bold_count=len(bold_files),
        t1w_count=len(t1w_files),
        nifti_count=len(all_nifti),
        sidecar_count=len(sidecars),
        missing_t1w_subjects=sorted(bold_subjects - t1w_subjects),
        missing_bold_subjects=sorted(t1w_subjects - bold_subjects),
        missing_sidecar_pairings=_missing_sidecars(all_nifti, sidecars),
        bids_entities=[
            parse_bids_entities(path).model_dump()
            for path in [*all_nifti, *sidecars]
        ],
        artifact_ids_by_type=artifacts_by_type,
        warnings=warnings,
    )


def _registry_safety_flags() -> dict[str, bool]:
    return {
        "rawdata_not_modified": True,
        "converted_input_not_modified": True,
        "no_external_tools_executed": True,
        "no_preprocessing_executed": True,
        "research_use_only": True,
        "clinical_use_prohibited": True,
    }


def _registry_root(
    *,
    project_dir: str,
    input_dir: Path,
    source_id: str,
    rawdata_dir: str,
) -> Path:
    project_root = Path(project_dir).expanduser().resolve() if project_dir else None
    if project_root:
        base = project_root
    elif input_dir.name.lower() in {"converted_bids", "bids"}:
        base = input_dir.resolve().parent
    else:
        base = Path("outputs").resolve()
    root = base / "preprocessing_inputs" / _safe_id(source_id, str(input_dir))
    if rawdata_dir:
        rawdata_root = Path(rawdata_dir).expanduser().resolve()
        if _is_relative_to(root, rawdata_root):
            raise ValueError("Artifact registry path would be inside rawdata.")
    return root


def _write_registry(path: Path, registry: PreprocessingArtifactRegistry) -> Path:
    payload = registry.model_dump(mode="json")
    return atomic_write_json(path, payload, schema_version=1)


def load_artifact_registry(path: str | Path) -> dict[str, Any]:
    registry_path = Path(path)
    return json.loads(registry_path.read_text(encoding="utf-8"))


def write_converted_input_registry(
    *,
    project_id: str,
    conversion_run_id: str,
    converted_bids_dir: str,
    project_dir: str = "",
    rawdata_dir: str = "",
    manifest_path: str | None = None,
    provenance_path: str | None = None,
    source_kind: str = "converted_bids",
) -> PreprocessingArtifactRegistryWriteResult:
    """Create a registry for converted BIDS/NIfTI preprocessing inputs."""
    warnings: list[str] = []
    blocking: list[str] = []
    input_dir = Path(converted_bids_dir).expanduser().resolve()
    if not input_dir.exists() or not input_dir.is_dir():
        return PreprocessingArtifactRegistryWriteResult(
            ok=False,
            status="blocked",
            blocking_issues=[f"Input directory not found: {converted_bids_dir}"],
        )

    project_root = Path(project_dir).expanduser().resolve() if project_dir else (
        input_dir.parent if input_dir.name.lower() in {"converted_bids", "bids"} else None
    )
    source_id = conversion_run_id or f"{source_kind}-{hashlib.sha256(str(input_dir).encode()).hexdigest()[:12]}"
    try:
        root = _registry_root(
            project_dir=project_dir,
            input_dir=input_dir,
            source_id=source_id,
            rawdata_dir=rawdata_dir,
        )
    except ValueError as exc:
        return PreprocessingArtifactRegistryWriteResult(
            ok=False,
            status="blocked",
            blocking_issues=[str(exc)],
        )
    root.mkdir(parents=True, exist_ok=True)

    created_at = _now_iso()
    bold_files, t1w_files, sidecars = _discover_files(input_dir)
    artifacts: list[PreprocessingArtifactRef] = []
    for path in [*bold_files, *t1w_files, *sidecars]:
        artifact_type = _artifact_type_for(path)
        artifacts.append(
            _make_artifact_ref(
                path,
                artifact_type=artifact_type,
                stage_id="dicom_conversion" if source_kind == "converted_bids" else "external_import",
                project_root=project_root,
                source_id=source_id,
                created_at=created_at,
                backend="dcm2niix" if source_kind == "converted_bids" else "external",
                provenance_path=provenance_path or "",
                metadata={"conversion_run_id": conversion_run_id} if conversion_run_id else {},
            )
        )

    if manifest_path:
        manifest = Path(manifest_path).expanduser()
        if manifest.exists():
            artifacts.append(
                _make_artifact_ref(
                    manifest,
                    artifact_type="stage_manifest",
                    stage_id="dicom_conversion",
                    project_root=project_root,
                    source_id=source_id,
                    created_at=created_at,
                    backend="dcm2niix",
                    provenance_path=provenance_path or "",
                    metadata={"conversion_run_id": conversion_run_id},
                )
            )
        else:
            warnings.append(f"Conversion manifest path not found: {manifest_path}")
    if provenance_path:
        provenance = Path(provenance_path).expanduser()
        if provenance.exists():
            artifacts.append(
                _make_artifact_ref(
                    provenance,
                    artifact_type="provenance_json",
                    stage_id="dicom_conversion",
                    project_root=project_root,
                    source_id=source_id,
                    created_at=created_at,
                    backend="dcm2niix",
                    provenance_path=provenance_path,
                    metadata={"conversion_run_id": conversion_run_id},
                )
            )
        else:
            warnings.append(f"Conversion provenance path not found: {provenance_path}")

    input_root_value, input_root_kind = _path_for_registry(input_dir, project_root)
    inventory = _inventory_from_artifacts(
        source_kind=source_kind,
        conversion_run_id=conversion_run_id,
        input_root=input_dir,
        input_root_value=input_root_value,
        input_root_kind=input_root_kind,
        bold_files=bold_files,
        t1w_files=t1w_files,
        sidecars=sidecars,
        artifacts=artifacts,
        warnings=warnings,
    )
    if inventory.nifti_count == 0:
        blocking.append("No NIfTI files found in preprocessing input.")
    if blocking:
        return PreprocessingArtifactRegistryWriteResult(
            ok=False,
            status="blocked",
            registry_root=str(root),
            blocking_issues=blocking,
            warnings=warnings,
        )

    registry_root_value, registry_root_kind = _path_for_registry(root, project_root)
    registry = PreprocessingArtifactRegistry(
        project_id=project_id,
        source_kind=source_kind,
        conversion_run_id=conversion_run_id,
        created_at=created_at,
        updated_at=created_at,
        registry_root=registry_root_value,
        registry_root_path_kind=registry_root_kind,
        input_inventory=inventory.model_dump(mode="json"),
        artifacts=artifacts,
        lineage={artifact.artifact_id: artifact.source_artifact_ids for artifact in artifacts},
        warnings=warnings,
        safety_flags=_registry_safety_flags(),
    )
    registry_path = _write_registry(root / REGISTRY_FILENAME, registry)
    artifacts_by_type: dict[str, int] = {}
    for artifact in artifacts:
        artifacts_by_type[artifact.artifact_type] = artifacts_by_type.get(artifact.artifact_type, 0) + 1
    return PreprocessingArtifactRegistryWriteResult(
        ok=True,
        status="registered",
        registry_path=str(registry_path),
        registry_root=str(root),
        artifact_count=len(artifacts),
        artifacts_by_type=artifacts_by_type,
        inventory=inventory.model_dump(mode="json"),
        warnings=warnings,
    )


def ensure_run_artifact_registry(
    *,
    project_id: str,
    preprocessing_run_id: str,
    run_dir: Path,
    input_dir: str,
    project_dir: str = "",
    source_registry_path: str = "",
    conversion_run_id: str = "",
    source_kind: str = "external_import",
) -> PreprocessingArtifactRegistryWriteResult:
    """Materialize the run-local registry, preserving input lineage."""
    run_dir.mkdir(parents=True, exist_ok=True)
    project_root = Path(project_dir).expanduser().resolve() if project_dir else None
    run_registry_path = run_dir / REGISTRY_FILENAME
    now = _now_iso()

    if source_registry_path and Path(source_registry_path).exists():
        data = load_artifact_registry(source_registry_path)
        data["preprocessing_run_id"] = preprocessing_run_id
        data["updated_at"] = now
        data.setdefault("source_kind", source_kind)
        data.setdefault("conversion_run_id", conversion_run_id)
        artifacts = data.get("artifacts", [])
        data["lineage"] = {
            str(item.get("artifact_id")): list(item.get("source_artifact_ids", []))
            for item in artifacts
            if isinstance(item, dict) and item.get("artifact_id")
        }
        atomic_write_json(run_registry_path, data, schema_version=1)
        artifacts_by_type: dict[str, int] = {}
        for artifact in artifacts:
            if isinstance(artifact, dict):
                typ = str(artifact.get("artifact_type") or "")
                artifacts_by_type[typ] = artifacts_by_type.get(typ, 0) + 1
        return PreprocessingArtifactRegistryWriteResult(
            ok=True,
            status="registered",
            registry_path=str(run_registry_path),
            registry_root=str(run_dir),
            artifact_count=len(artifacts),
            artifacts_by_type=artifacts_by_type,
            inventory=data.get("input_inventory", {}),
            warnings=list(data.get("warnings", [])),
        )

    built = write_converted_input_registry(
        project_id=project_id,
        conversion_run_id=conversion_run_id,
        converted_bids_dir=input_dir,
        project_dir=project_dir,
        source_kind=source_kind,
    )
    if not built.ok:
        return built
    data = load_artifact_registry(built.registry_path)
    data["preprocessing_run_id"] = preprocessing_run_id
    data["updated_at"] = now
    root_value, root_kind = _path_for_registry(run_dir, project_root)
    data["registry_root"] = root_value
    data["registry_root_path_kind"] = root_kind
    atomic_write_json(run_registry_path, data, schema_version=1)
    built.registry_path = str(run_registry_path)
    built.registry_root = str(run_dir)
    return built


def update_run_registry_inventory(
    registry_path: str | Path,
    inventory: dict[str, Any],
) -> None:
    path = Path(registry_path)
    if not path.exists():
        return
    data = load_artifact_registry(path)
    data["input_inventory"] = inventory
    data["updated_at"] = _now_iso()
    atomic_write_json(path, data, schema_version=1)


def _latest_source_artifacts(data: dict[str, Any], stage_id: str) -> list[str]:
    try:
        spec = get_preprocessing_stage_spec(stage_id)
        input_types = set(spec.input_artifact_types)
    except KeyError:
        input_types = set()
    source_ids: list[str] = []
    for artifact in data.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        if input_types and artifact.get("artifact_type") not in input_types:
            continue
        artifact_id = str(artifact.get("artifact_id") or "")
        if artifact_id and artifact_id not in source_ids:
            source_ids.append(artifact_id)
    return source_ids


def append_stage_output_artifacts(
    *,
    registry_path: str | Path,
    project_id: str,
    preprocessing_run_id: str,
    stage_id: str,
    output_paths_by_type: dict[str, list[Path]],
    project_dir: str = "",
    source_execution_id: str = "",
    backend: str = "",
    provenance_path: str = "",
    qc_path: str = "",
    metadata: dict[str, Any] | None = None,
    source_artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Append stage output references to a run registry."""
    path = Path(registry_path)
    if path.exists():
        data = load_artifact_registry(path)
    else:
        data = {
            "registry_schema_version": "1",
            "project_id": project_id,
            "preprocessing_run_id": preprocessing_run_id,
            "source_kind": "run_stage_outputs",
            "created_at": _now_iso(),
            "input_inventory": {},
            "artifacts": [],
            "lineage": {},
            "warnings": [],
            "safety_flags": _registry_safety_flags(),
        }
    project_root = Path(project_dir).expanduser().resolve() if project_dir else None
    source_ids = _latest_source_artifacts(data, stage_id)
    for artifact_id in source_artifact_ids or []:
        value = str(artifact_id).strip()
        if value and value not in source_ids:
            source_ids.append(value)
    created_at = _now_iso()
    appended: list[PreprocessingArtifactRef] = []
    for artifact_type, paths in output_paths_by_type.items():
        for output_path in paths:
            appended.append(
                _make_artifact_ref(
                    output_path,
                    artifact_type=artifact_type,
                    stage_id=stage_id,
                    project_root=project_root,
                    source_id=f"{preprocessing_run_id}:{source_execution_id or stage_id}",
                    created_at=created_at,
                    backend=backend,
                    source_artifact_ids=source_ids,
                    provenance_path=provenance_path,
                    qc_path=qc_path,
                    metadata={
                        **(metadata or {}),
                        "source_execution_id": source_execution_id,
                    },
                )
            )
    existing_ids = {
        str(item.get("artifact_id"))
        for item in data.get("artifacts", [])
        if isinstance(item, dict)
    }
    new_items = [
        artifact.model_dump(mode="json")
        for artifact in appended
        if artifact.artifact_id not in existing_ids
    ]
    data.setdefault("artifacts", []).extend(new_items)
    if new_items:
        safety_flags = dict(data.get("safety_flags") or {})
        safety_flags["no_preprocessing_executed"] = False
        data["safety_flags"] = safety_flags
    lineage = data.setdefault("lineage", {})
    for artifact in appended:
        lineage[artifact.artifact_id] = artifact.source_artifact_ids
    data["project_id"] = project_id
    data["preprocessing_run_id"] = preprocessing_run_id
    data["updated_at"] = created_at
    atomic_write_json(path, data, schema_version=1)
    return {
        "ok": True,
        "registry_path": str(path),
        "appended_artifact_count": len(new_items),
        "appended_artifact_ids": [item["artifact_id"] for item in new_items],
    }


__all__ = [
    "REGISTRY_FILENAME",
    "append_stage_output_artifacts",
    "ensure_run_artifact_registry",
    "load_artifact_registry",
    "parse_bids_entities",
    "update_run_registry_inventory",
    "write_converted_input_registry",
]
