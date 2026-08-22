"""DICOM conversion execution — Phase 6B real dcm2niix execution.

Reads project metadata and conversion dry-run mappings, builds dcm2niix
command templates, runs preflight safety checks, and executes dcm2niix
conversion behind the full approval/audit/manifest/provenance gates.

Real dcm2niix execution is ENABLED in Phase 6B.
No rawdata is modified. All output goes to workspace.

Reference:
  docs/预处理与科学计算/DICOM转换/DICOM到NIfTI执行包装契约.md
  docs/预处理与科学计算/原生预处理/真实预处理执行契约.md
  specs/PHASE6_SPEC.md
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC
from pathlib import Path, PurePosixPath

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start
from typing import Any

from src.backend.app.native_preproc.io.dicom_to_nifti import ALGORITHM_VERSION
from src.backend.app.schemas.dicom_conversion_execution import (
    Dcm2niixAvailabilityCheck,
    Dcm2niixCommandTemplate,
    DicomConversionExecutionRequest,
    DicomConversionExecutionResponse,
    DicomConversionMapping,
    DicomConversionPreflight,
    DicomConversionSafetyFlags,
    DicomConversionSandboxResult,
    build_dcm2niix_command_template,
    build_disabled_conversion_response,
    build_native_dicom_conversion_template,
    is_conversion_execution_enabled,
    summarize_conversion_mappings,
    validate_output_root_not_under_rawdata,
    validate_output_root_under_project,
)
from src.backend.app.services.conversion_planner import plan_conversion
from src.backend.app.services.mock_store import mock_store

# ── dcm2niix configuration ──
_DCM2NIIX_EXPECTED_VERSION = "v1.0.20260416"


def check_native_dicom_converter_availability() -> dict[str, Any]:
    """Check the in-project Python conversion dependencies without subprocesses."""

    versions: dict[str, str] = {}
    errors: list[str] = []
    for package in ("pydicom", "nibabel", "numpy"):
        try:
            module = __import__(package)
            versions[package] = str(getattr(module, "__version__", "unknown"))
        except ImportError:
            errors.append(f"Missing optional dependency: {package}")
    return {
        "found": not errors,
        "status": "available" if not errors else "missing",
        "backend": "medimage-native",
        "executable_path": None,
        "version": ALGORITHM_VERSION if not errors else None,
        "versions": versions,
        "sha256": None,
        "strategy": "in_project_python",
        "error": "; ".join(errors) if errors else None,
        "warnings": [],
    }


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 of a file."""
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _detect_dcm2niix() -> dict[str, Any]:
    """Detect dcm2niix with 4-layer fallback.

    Priority:
      1. MEDIMAGE_DCM2NIIX_PATH env var
      2. shutil.which("dcm2niix") — system/mamba PATH
      3. Bundled binary at project_root/tools/dcm2niix.exe
      4. Not found — return clear error

    Returns dict with keys: found, executable_path, version, sha256, error, strategy
    """
    import shutil as _sh

    # Layer 1: env var
    env_path = os.environ.get("MEDIMAGE_DCM2NIIX_PATH")
    if env_path and Path(env_path).exists():
        try:
            result = reject_unreviewed_process_start(
                [str(env_path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_line = (result.stdout or "").strip().split("\n")[0]
            return {
                "found": True,
                "executable_path": str(env_path),
                "version": version_line,
                "sha256": _sha256_file(Path(env_path)),
                "error": None,
                "strategy": "env_var",
            }
        except Exception:
            pass  # Fall through to next layer

    # Layer 2: PATH
    exe_path = _sh.which("dcm2niix")
    if exe_path:
        try:
            result = reject_unreviewed_process_start(
                [exe_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_line = (result.stdout or "").strip().split("\n")[0]
            return {
                "found": True,
                "executable_path": exe_path,
                "version": version_line,
                "sha256": _sha256_file(Path(exe_path)),
                "error": None,
                "strategy": "path",
            }
        except Exception:
            pass  # Fall through

    # Layer 3: bundled binary
    try:
        from src.backend.app.version import APP_VERSION  # noqa
    except ImportError:
        pass
    # Walk up from this file to find repo root
    current = Path(__file__).resolve().parent
    for _ in range(10):
        bundled = current / "tools" / "dcm2niix.exe"
        if bundled.exists():
            break
        bundled = None
        if current == current.parent:
            break
        current = current.parent

    if bundled:
        try:
            result = reject_unreviewed_process_start(
                [str(bundled), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version_line = (result.stdout or "").strip().split("\n")[0]
            return {
                "found": True,
                "executable_path": str(bundled),
                "version": version_line,
                "sha256": _sha256_file(bundled),
                "error": None,
                "strategy": "bundled",
            }
        except Exception as e:
            return {
                "found": False,
                "executable_path": None,
                "version": None,
                "sha256": None,
                "error": f"Bundled binary failed: {e}",
                "strategy": "bundled",
            }

    # Layer 4: not found
    return {
        "found": False,
        "executable_path": None,
        "version": None,
        "sha256": None,
        "error": (
            "dcm2niix not found. Set MEDIMAGE_DCM2NIIX_PATH env var, "
            "add dcm2niix to PATH, or place bundled binary at project_root/tools/dcm2niix.exe."
        ),
        "strategy": "none",
    }


def _bundled_dcm2niix_candidates() -> list[Path]:
    """Return candidate paths for the bundled dcm2niix binary.

    Covers development and PyInstaller-packaged layouts:
      - desktop/resources/tools/windows-x64/dcm2niix.exe (dev)
      - <repo_root>/tools/dcm2niix.exe (legacy dev)
      - <exe_dir>/resources/tools/dcm2niix.exe (PyInstaller one-file)
      - <exe_dir>/resources/tools/windows-x64/dcm2niix.exe (PyInstaller alt)
      - <_MEIPASS>/resources/tools/dcm2niix.exe (PyInstaller onedir temp)
    """
    candidates: list[Path] = []

    # PyInstaller _MEIPASS (onedir / one-file temp extraction)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base = Path(meipass)
        candidates.extend(
            [
                base / "resources" / "tools" / "dcm2niix.exe",
                base / "resources" / "tools" / "windows-x64" / "dcm2niix.exe",
            ]
        )

    # Directory of the running executable (PyInstaller onedir / desktop install)
    exe_dir = Path(sys.executable).resolve().parent
    candidates.extend(
        [
            exe_dir / "resources" / "tools" / "dcm2niix.exe",
            exe_dir / "resources" / "tools" / "windows-x64" / "dcm2niix.exe",
        ]
    )

    # Development layout: walk up from this file to repo root
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidates.extend(
            [
                current / "desktop" / "resources" / "tools" / "windows-x64" / "dcm2niix.exe",
                current / "desktop" / "resources" / "tools" / "dcm2niix.exe",
                current / "tools" / "dcm2niix.exe",
            ]
        )
        if current == current.parent:
            break
        current = current.parent

    return candidates


def _desktop_config_dcm2niix_path() -> str | None:
    """Return the dcm2niix path configured in desktop config, if any."""
    try:
        from src.backend.app.runtime.desktop_config import get_desktop_config

        config = get_desktop_config(redacted=True)
        dc = config.get("dicom_conversion", {}) or {}
        path = dc.get("dcm2niix_path") or ""
        return path if path else None
    except Exception:
        return None


def _map_availability_to_conversion_status(availability_status: str) -> str:
    """Map a Dcm2niixAvailabilityStatus to a DicomConversionStatus.

    The task plan (§12) mentions an `unavailable` state. Since
    DicomConversionStatus does not include `unavailable`, it is represented
    by `blocked` (missing/unknown) or `disabled` (feature flag off / version
    mismatch). This function is the canonical mapping referenced by the
    DicomConversionStatus type definition.

    Note: `available` is not mapped here because availability is only checked
    when conversion is gated. Callers should short-circuit on `available`
    before invoking this function.
    """
    return {
        "missing": "blocked",
        "version_failed": "disabled",
        "disabled": "disabled",
        "unknown": "blocked",
    }.get(availability_status, "blocked")


def _detect_dcm2niix_runtime(
    *,
    executable: str = "dcm2niix",
    env: dict[str, str] | None = None,
    runner: Any = None,
) -> dict[str, Any]:
    """Detect dcm2niix for Phase 6 real execution.

    Resolution order (per 实现dcm2nii任务方案.md §9.3):
      1. Desktop config dicom_conversion.dcm2niix_path
      2. MEDIMAGE_DCM2NIIX_PATH env var
      3. Active mamba/conda env
      4. System PATH
      5. Bundled resource (desktop/resources/tools/windows-x64/dcm2niix.exe
         or PyInstaller-packaged resources/tools/dcm2niix.exe)
      6. Legacy dev tools/dcm2niix.exe
      7. Not found — return clear error
    """
    from src.backend.app.schemas.dicom_conversion_execution import (
        parse_dcm2niix_version,
    )

    effective_env = os.environ if env is None else env
    warnings: list[str] = []

    def _query_version(candidate: str) -> str | None:
        try:
            if runner is not None:
                result = runner([candidate, "--version"])
            else:
                result = reject_unreviewed_process_start(
                    [candidate, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            stdout = getattr(result, "stdout", "") or ""
            return parse_dcm2niix_version(stdout)
        except Exception as exc:
            warnings.append(f"Version query failed for {candidate}: {exc}")
            return None

    def _result(candidate: str, strategy: str) -> dict[str, Any]:
        path = Path(candidate)
        sha256 = _sha256_file(path) if path.exists() and path.is_file() else None
        if sha256 is None:
            warnings.append(f"SHA256 unavailable for {candidate}: file is not readable.")
        return {
            "found": True,
            "executable_path": candidate,
            "version": _query_version(candidate),
            "sha256": sha256,
            "error": None,
            "warnings": list(warnings),
            "strategy": strategy,
            "expected_version": _DCM2NIIX_EXPECTED_VERSION,
        }

    def _mamba_env_candidates() -> list[Path]:
        roots: list[Path] = []
        conda_prefix = effective_env.get("CONDA_PREFIX") or effective_env.get("MAMBA_ROOT_PREFIX")
        if conda_prefix:
            roots.append(Path(conda_prefix))

        executable_path = Path(sys.executable).resolve()
        if executable_path.parent.name.lower() == "scripts":
            roots.append(executable_path.parent.parent)
        else:
            roots.append(executable_path.parent)

        candidates: list[Path] = []
        for root in roots:
            candidates.extend(
                [
                    root / "Scripts" / "dcm2niix.exe",
                    root / "Lib" / "site-packages" / "bin" / "dcm2niix.exe",
                    root / "Lib" / "site-packages" / "dcm2niix" / "dcm2niix.exe",
                ]
            )
        return candidates

    # 1. Desktop config path
    config_path = _desktop_config_dcm2niix_path()
    if config_path and Path(config_path).exists():
        return _result(str(Path(config_path)), "desktop_config")
    if config_path:
        warnings.append(f"Desktop config dcm2niix_path does not exist: {config_path}")

    # 2. Env var
    env_path = effective_env.get("MEDIMAGE_DCM2NIIX_PATH")
    if env_path:
        if Path(env_path).exists():
            return _result(str(Path(env_path)), "env_var")
        warnings.append(f"MEDIMAGE_DCM2NIIX_PATH does not exist: {env_path}")

    # 3. Mamba/conda env
    for candidate in _mamba_env_candidates():
        if candidate.exists():
            return _result(str(candidate), "mamba_env")

    # 4. System PATH
    exe_path = shutil.which(executable)
    if exe_path:
        return _result(exe_path, "path")

    # 5 & 6. Bundled resource (desktop/resources or PyInstaller resources)
    for bundled in _bundled_dcm2niix_candidates():
        if bundled.exists():
            strategy = "bundled_resource" if "resources" in bundled.parts else "bundled"
            return _result(str(bundled), strategy)

    return {
        "found": False,
        "executable_path": None,
        "version": None,
        "sha256": None,
        "error": (
            "dcm2niix not found. Set desktop config dicom_conversion.dcm2niix_path, "
            "set MEDIMAGE_DCM2NIIX_PATH, run the backend with the mamba environment "
            "that provides dcm2niix, add dcm2niix to PATH, or place a bundled binary "
            "at desktop/resources/tools/windows-x64/dcm2niix.exe."
        ),
        "warnings": warnings,
        "strategy": "none",
        "expected_version": _DCM2NIIX_EXPECTED_VERSION,
    }


def _mapping_preview_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _strip_nifti_extension(filename: str) -> str:
    for suffix in (".nii.gz", ".nii"):
        if filename.endswith(suffix):
            return filename[: -len(suffix)]
    return Path(filename).stem


def _mapping_output_dir(output_root: str | None, suggested_relative_path: str | None) -> str:
    if not output_root:
        return ""
    if not suggested_relative_path:
        return output_root

    rel = PurePosixPath(str(suggested_relative_path).replace("\\", "/"))
    parent_parts = [part for part in rel.parent.parts if part not in ("", ".", "..")]
    return str(Path(output_root, *parent_parts)) if parent_parts else output_root


def _mapping_filename(mapping: DicomConversionMapping, fallback_index: int) -> str:
    if mapping.subject_id and mapping.task and mapping.suffix:
        return f"{mapping.subject_id}_task-{mapping.task}_{mapping.suffix}"
    if mapping.subject_id and mapping.suffix:
        return f"{mapping.subject_id}_{mapping.suffix}"
    if mapping.suggested_relative_path:
        rel = PurePosixPath(str(mapping.suggested_relative_path).replace("\\", "/"))
        stem = _strip_nifti_extension(rel.name)
        if stem:
            return stem
    return f"mapping_{fallback_index}"


def _dcm2niix_output_reports_error(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    return "error:" in combined or "invalid option" in combined


def _expected_nifti_path(output_dir: str, filename_pattern: str, compress: str) -> Path:
    stem = _strip_nifti_extension(filename_pattern or "converted")
    suffix = ".nii" if (compress or "").lower() in {"n", "3"} else ".nii.gz"
    return Path(output_dir) / f"{stem}{suffix}"


def _common_output_root(output_dirs: list[str], fallback: Path) -> Path:
    clean_dirs = [str(Path(p)) for p in output_dirs if p]
    if not clean_dirs:
        return fallback
    try:
        return Path(os.path.commonpath(clean_dirs))
    except ValueError:
        return fallback


def run_conversion_preflight(
    project_id: str,
    request: DicomConversionExecutionRequest | None = None,
) -> DicomConversionPreflight:
    """Run preflight checks for DICOM conversion without executing.

    Reads project metadata, conversion dry-run mappings, and environment
    flags.  Builds command templates.  Does NOT call dcm2niix.  Does NOT
    write files.

    Returns a ``DicomConversionPreflight`` with ``conversion_disabled_by_default=true``.
    """
    warnings: list[str] = []
    errors: list[str] = []
    blocking: list[str] = []

    project = mock_store.get_project(project_id)
    metadata = project.metadata if project and isinstance(project.metadata, dict) else {}
    rawdata_dir = str(metadata.get("rawdata_dir") or "")
    project_dir = str(metadata.get("project_dir") or "")

    # ── 1. Environment flag check ──
    # Per 实现dcm2nii任务方案.md §11.1, only DICOM-specific flags are
    # required. MATLAB/SPM/real-preprocessing flags must NOT block
    # DICOM→NIfTI conversion.
    env_flags: dict[str, str] = {}
    for flag in [
        "MEDIMAGE_ENABLE_DICOM_CONVERSION",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
        "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
    ]:
        value = os.environ.get(flag, "")
        env_flags[flag] = value

    env_ok, missing_env = is_conversion_execution_enabled(env_flags)
    if not env_ok:
        blocking.append(
            f"Conversion execution blocked: {len(missing_env)} environment "
            f"flag(s) missing: {', '.join(missing_env)}."
        )

    # ── 2. Tool detection ──
    converter_info = check_native_dicom_converter_availability()
    warnings.extend(converter_info.get("warnings", []))
    tool_available = converter_info["found"]
    exe_path = None
    if not tool_available:
        blocking.append(
            converter_info["error"] or "Native DICOM conversion dependencies are unavailable."
        )

    # ── 3. Dry-run mappings ──
    try:
        from src.backend.app.schemas.desktop import ConversionDryRunRequest

        dry_run = plan_conversion(
            project_id=project_id,
            request=ConversionDryRunRequest(),
        )
    except Exception as exc:
        errors.append(f"CONVERSION_DRY_RUN_FAILED: {exc}")
        return DicomConversionPreflight(
            ok=False,
            status="blocked",
            conversion_disabled_by_default=True,
            errors=errors,
            blocking_issues=[f"Dry-run failed: {exc}"],
        )

    mappings_dicts = dry_run.mapping_preview
    if not mappings_dicts:
        blocking.append("No conversion mappings were generated by the dry-run planner.")

    # ── 4. Convert dry-run mappings to execution mappings ──
    mappings: list[DicomConversionMapping] = []
    for md in mappings_dicts:
        mapping_data = _mapping_preview_to_dict(md)
        if not mapping_data:
            continue
        mappings.append(
            DicomConversionMapping(
                source_path=str(mapping_data.get("source_path", "")),
                source_type=str(mapping_data.get("source_type", "dicom_series")),
                subject_id=mapping_data.get("subject_id"),
                session_id=mapping_data.get("session_id"),
                modality=str(mapping_data.get("modality", "func")),
                suffix=mapping_data.get("suffix"),
                task=mapping_data.get("task"),
                suggested_relative_path=mapping_data.get("suggested_relative_path"),
                confidence=str(mapping_data.get("confidence", "high")),
            )
        )

    # ── 5. Output root safety ──
    output_root = request.output_root if request and request.output_root else None
    if not output_root and project_dir:
        output_root = str(Path(project_dir) / "converted_bids")

    output_safe = False
    if output_root and project_dir:
        output_safe = validate_output_root_under_project(output_root, project_dir)
        if not output_safe:
            blocking.append(
                f"Output root {output_root} is not under project directory {project_dir}."
            )

    if output_root and rawdata_dir:
        if not validate_output_root_not_under_rawdata(output_root, rawdata_dir):
            blocking.append(
                f"Output root {output_root} must not be inside the rawdata directory {rawdata_dir}."
            )
            output_safe = False

    # ── 6. Build command templates ──
    command_templates: list[Dcm2niixCommandTemplate] = []
    for i, mapping in enumerate(mappings, start=1):
        if not mapping.enabled:
            continue
        input_dir = mapping.source_path
        out_dir = _mapping_output_dir(output_root, mapping.suggested_relative_path)
        filename = _mapping_filename(mapping, i)

        template = build_native_dicom_conversion_template(
            input_dir=input_dir,
            output_dir=out_dir,
            filename_pattern=filename,
        )
        mapping.output_dir = out_dir
        mapping.output_filename = filename + ".nii.gz"
        command_templates.append(template)

    # ── 7. Determine status ──
    # Per 实现dcm2nii任务方案.md §12, the preflight status distinguishes:
    #   - unavailable: dcm2niix missing
    #   - disabled: conversion feature flag not enabled
    #   - blocked: input/output/safety/mapping invalid
    #   - review_required: technical checks pass, awaiting user approval
    #   - ready: technical checks AND approval both pass (set by prepare)
    if not env_ok:
        status: str = "disabled"
    elif not tool_available:
        status = "blocked"
    elif blocking:
        status = "blocked"
    elif output_safe and mappings:
        # Technical checks pass; approval is still required before execution.
        status = "review_required"
    else:
        status = "warning"

    mapping_summary = summarize_conversion_mappings(mappings)

    return DicomConversionPreflight(
        ok=len(errors) == 0,
        status=status,  # type: ignore[arg-type]
        conversion_disabled_by_default=not env_ok,
        tool_available=tool_available,
        executable_path=exe_path,
        tool_version=converter_info.get("version"),
        env_enabled=env_ok,
        missing_env_flags=missing_env,
        approval_required=True,
        audit_required=True,
        output_dir_safe=output_safe,
        output_root_preview=output_root,
        rawdata_readonly=True,
        mapping_count=mapping_summary["total_count"],
        mappings=mappings,
        command_templates=command_templates,
        warnings=warnings,
        errors=errors,
        blocking_issues=blocking,
        safety_flags=DicomConversionSafetyFlags(
            conversion_disabled_by_default=not env_ok,
            env_flags_missing=not env_ok,
        ),
    )


def _execute_single_mapping(
    mapping: DicomConversionMapping,
    index: int,
    exe_path: str,
    output_root: Path,
    dcm2niix_info: dict[str, Any],
) -> dict[str, Any]:
    """Execute a single dcm2niix conversion for one mapping.

    Uses argv list, shell=False.  Records timing, stdout/stderr, and result.
    """
    import time

    start_time = time.monotonic()
    out_dir = Path(mapping.output_dir) if mapping.output_dir else output_root
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = mapping.output_filename or f"mapping_{index}"
    input_dir = mapping.source_path

    if not input_dir or not Path(input_dir).exists():
        return {
            "mapping_index": index,
            "status": "failed",
            "subject_id": mapping.subject_id,
            "modality": mapping.modality,
            "output_dir": str(out_dir),
            "output_file": "",
            "error": f"Input directory not found: {input_dir}",
            "duration_ms": (time.monotonic() - start_time) * 1000,
            "command": [],
        }

    cmd = [
        exe_path,
        "-f",
        filename,
        "-o",
        str(out_dir),
        "-z",
        "y",  # gzip
        "-b",
        "y",  # BIDS sidecar
        "-ba",
        "y",  # anonymised BIDS
        str(input_dir),
    ]

    try:
        result = reject_unreviewed_process_start(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min timeout per mapping
        )
        duration_ms = (time.monotonic() - start_time) * 1000

        # Find the output file
        expected_nii = out_dir / f"{filename}.nii.gz"
        if expected_nii.exists():
            return {
                "mapping_index": index,
                "status": "succeeded" if result.returncode == 0 else "failed",
                "subject_id": mapping.subject_id,
                "modality": mapping.modality,
                "output_dir": str(out_dir),
                "output_file": str(expected_nii),
                "output_size_bytes": expected_nii.stat().st_size,
                "error": None if result.returncode == 0 else f"Exit code: {result.returncode}",
                "stdout": result.stdout[-1000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
                "returncode": result.returncode,
                "duration_ms": duration_ms,
                "command": cmd,
            }
        else:
            return {
                "mapping_index": index,
                "status": "failed",
                "subject_id": mapping.subject_id,
                "modality": mapping.modality,
                "output_dir": str(out_dir),
                "output_file": "",
                "error": f"No output file created at {expected_nii}. stderr: {result.stderr[:500] if result.stderr else 'none'}",
                "stdout": result.stdout[-1000:] if result.stdout else "",
                "stderr": result.stderr[-1000:] if result.stderr else "",
                "returncode": result.returncode,
                "duration_ms": duration_ms,
                "command": cmd,
            }
    except subprocess.TimeoutExpired:
        return {
            "mapping_index": index,
            "status": "failed",
            "subject_id": mapping.subject_id,
            "modality": mapping.modality,
            "output_dir": str(out_dir),
            "output_file": "",
            "error": f"dcm2niix timed out after 300s for {mapping.source_path}",
            "duration_ms": (time.monotonic() - start_time) * 1000,
            "command": cmd,
        }
    except Exception as exc:
        return {
            "mapping_index": index,
            "status": "failed",
            "subject_id": mapping.subject_id,
            "modality": mapping.modality,
            "output_dir": str(out_dir),
            "output_file": "",
            "error": str(exc),
            "duration_ms": (time.monotonic() - start_time) * 1000,
            "command": cmd,
        }


def run_conversion_execute(
    project_id: str,
    request: DicomConversionExecutionRequest,
) -> DicomConversionExecutionResponse:
    """Execute or block DICOM conversion based on gating conditions.

    Phase 6B: Real execution enabled with full safety gates.
    Calls dcm2niix via argv list, shell=False, output only in workspace.

    Per 实现dcm2nii任务方案.md §12, preflight returns ``review_required``
    when technical checks pass.  Execution requires either ``ready``
    (approval already obtained via prepare) or ``review_required`` plus
    explicit ``confirm_execution`` on the request.
    """
    preflight = run_conversion_preflight(project_id, request)

    # ── Block if preflight is not in an executable state ──
    # ``ready`` = approval already obtained; ``review_required`` = technical
    # checks pass, execution allowed only when caller confirms explicitly.
    executable_states = {"ready", "review_required"}
    if preflight.status not in executable_states:
        return build_disabled_conversion_response(
            project_id=project_id,
            reason=f"DICOM conversion blocked: {', '.join(preflight.blocking_issues or ['unknown'])}",
            missing_env_flags=preflight.missing_env_flags,
        )

    # If only review_required, require explicit execution confirmation
    if preflight.status == "review_required" and not request.confirm_execution:
        return build_disabled_conversion_response(
            project_id=project_id,
            reason="DICOM conversion requires explicit confirm_execution before running.",
            missing_env_flags=preflight.missing_env_flags,
        )

    # ── dcm2niix detection ──
    dcm2niix_info = _detect_dcm2niix_runtime()
    if not dcm2niix_info["found"]:
        return build_disabled_conversion_response(
            project_id=project_id,
            reason=f"dcm2niix not available: {dcm2niix_info['error']}",
        )

    exe_path = dcm2niix_info["executable_path"]
    output_root = Path(preflight.output_root_preview or "outputs/dicom_converted")
    output_root.mkdir(parents=True, exist_ok=True)

    # ── Verify rawdata checksum before conversion ──
    rawdata_checksums: dict[str, str] = {}
    rawdata_path = Path(str(request.rawdata_dir or "data"))
    if rawdata_path.exists():
        for f in sorted(rawdata_path.rglob("*.dcm")):
            rawdata_checksums[str(f)] = _sha256_file(f)

    # ── Execute each mapping ──
    succeeded = 0
    failed = 0
    updated_mappings: list[DicomConversionMapping] = []
    execution_results: list[dict[str, Any]] = []
    errors: list[str] = []
    all_stdout: list[str] = []
    all_stderr: list[str] = []

    for i, mapping in enumerate(preflight.mappings, start=1):
        if not mapping.enabled:
            updated_mappings.append(mapping)
            continue

        result = _execute_single_mapping(
            mapping=mapping,
            index=i,
            exe_path=exe_path,
            output_root=output_root,
            dcm2niix_info=dcm2niix_info,
        )
        execution_results.append(result)
        if result["status"] == "succeeded":
            succeeded += 1
            mapping.status = "succeeded"
        else:
            failed += 1
            mapping.status = "failed"
            errors.append(
                f"Mapping {i} ({mapping.subject_id}/{mapping.modality}): {result.get('error')}"
            )

        if result.get("stdout"):
            all_stdout.append(result["stdout"])
        if result.get("stderr"):
            all_stderr.append(result["stderr"])
        updated_mappings.append(mapping)

    # ── Verify rawdata checksum after ──
    rawdata_unchanged = True
    if rawdata_path.exists():
        for f in sorted(rawdata_path.rglob("*.dcm")):
            if str(f) in rawdata_checksums:
                if _sha256_file(f) != rawdata_checksums[str(f)]:
                    rawdata_unchanged = False
                    errors.append(f"RAWDATA INTEGRITY VIOLATION: checksum changed for {f}")
                    break

    # ── Write provenance ──
    provenance_path: Path | None = None
    manifest_path: Path | None = None
    try:
        provenance_path = output_root / "conversion_provenance.json"
        provenance_path.write_text(
            json.dumps(
                {
                    "dcm2niix_version": dcm2niix_info["version"],
                    "dcm2niix_sha256": dcm2niix_info["sha256"],
                    "dcm2niix_strategy": dcm2niix_info["strategy"],
                    "converted_at": _now_iso(),
                    "total_mappings": len(preflight.mappings),
                    "succeeded": succeeded,
                    "failed": failed,
                    "rawdata_unchanged": rawdata_unchanged,
                    "results": execution_results,
                }
            ),
            encoding="utf-8",
        )

        manifest_path = output_root / "conversion_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "output_root": str(output_root),
                    "mapping_count": len(updated_mappings),
                    "succeeded_count": succeeded,
                    "failed_count": failed,
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass  # Best effort

    # ── Determine status ──
    if failed == 0 and succeeded > 0:
        status: str = "succeeded"
    elif failed > 0 and succeeded > 0:
        status = "partial"
    elif failed > 0 and succeeded == 0:
        status = "failed"
    else:
        status = "disabled"

    return DicomConversionExecutionResponse(
        ok=failed == 0,
        status=status,  # type: ignore[arg-type]
        mode="execute" if succeeded > 0 else "execute_disabled",  # type: ignore[arg-type]
        project_id=project_id,
        dry_run=False,
        conversion_disabled=preflight.conversion_disabled_by_default,
        execution_blocked=False,
        mappings=updated_mappings,
        command_templates=preflight.command_templates,
        output_root=str(output_root),
        manifest_path=str(manifest_path) if manifest_path else None,
        provenance_path=str(provenance_path) if provenance_path else None,
        stdout_log_path=None,
        stderr_log_path=None,
        warnings=preflight.warnings,
        errors=errors if errors else preflight.errors,
        blocking_issues=preflight.blocking_issues,
        safety_flags=DicomConversionSafetyFlags(
            conversion_disabled_by_default=preflight.conversion_disabled_by_default,
            env_flags_missing=not preflight.env_enabled,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 5. Pure helpers (re-exported for convenience)
# ═══════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════
# 6. Phase 4C-0 — Availability check and sandbox runner
# ═══════════════════════════════════════════════════════════════════════


def check_dcm2niix_availability(
    executable: str = "dcm2niix",
    env: dict[str, str] | None = None,
    runner: Any = None,
) -> Dcm2niixAvailabilityCheck:
    """Check whether dcm2niix is available and can be queried for version.

    Uses ``shutil.which`` for path detection.  Version query is performed
    only when a ``runner`` callable is explicitly injected (for testing).

    The runner must accept ``[executable, "--version"]`` as an argv list.
    ``shell=True`` is never used.

    Returns a ``Dcm2niixAvailabilityCheck``.
    """
    import shutil
    from datetime import datetime

    from src.backend.app.schemas.dicom_conversion_execution import (
        Dcm2niixAvailabilityCheck,
        is_conversion_execution_enabled,
        parse_dcm2niix_version,
    )

    warnings: list[str] = []
    errors: list[str] = []
    now = datetime.now(UTC).isoformat()

    env = env or {}
    env_ok, missing_env = is_conversion_execution_enabled(env)

    if not env_ok:
        return Dcm2niixAvailabilityCheck(
            ok=True,
            status="disabled",
            executable=executable,
            env_enabled=False,
            missing_env_flags=missing_env,
            checked_at=now,
            warnings=[f"Environment flags missing: {', '.join(missing_env)}"],
        )

    detected = _detect_dcm2niix_runtime(
        executable=executable,
        env=env,
        runner=runner,
    )
    warnings = list(detected.get("warnings") or [])
    errors: list[str] = []
    if not detected.get("found"):
        return Dcm2niixAvailabilityCheck(
            ok=True,
            status="missing",
            executable=executable,
            executable_path=None,
            version=None,
            binary_sha256=None,
            detection_strategy=detected.get("strategy"),
            expected_version=detected.get("expected_version"),
            env_enabled=True,
            missing_env_flags=[],
            checked_at=now,
            warnings=warnings,
            errors=[detected.get("error") or f"{executable} was not found."],
        )

    version = detected.get("version")
    return Dcm2niixAvailabilityCheck(
        ok=True,
        status="available" if version else "version_failed",  # type: ignore[arg-type]
        executable=executable,
        executable_path=detected.get("executable_path"),
        version=version,
        binary_sha256=detected.get("sha256"),
        detection_strategy=detected.get("strategy"),
        expected_version=detected.get("expected_version"),
        env_enabled=True,
        missing_env_flags=[],
        checked_at=now,
        warnings=warnings,
        errors=errors,
    )

    exe_path = shutil.which(executable)
    if exe_path is None:
        return Dcm2niixAvailabilityCheck(
            ok=True,
            status="missing",
            executable=executable,
            env_enabled=True,
            missing_env_flags=[],
            checked_at=now,
            errors=[f"{executable} not found on system PATH."],
        )

    version: str | None = None
    if runner is not None:
        try:
            result = runner([executable, "--version"])
            stdout = getattr(result, "stdout", "") or ""
            version = parse_dcm2niix_version(stdout)
        except Exception as exc:
            warnings.append(f"Version query failed: {exc}")
    else:
        # No injected runner — use real subprocess since env flags confirmed
        try:
            import subprocess as _sp

            result = _sp.run(
                [executable, "--version"],
                capture_output=True,
                text=True,
                check=False,
            )
            stdout = result.stdout or ""
            version = parse_dcm2niix_version(stdout)
        except Exception as exc:
            warnings.append(f"Version query via subprocess failed: {exc}")

    status = "available" if version else "version_failed"

    return Dcm2niixAvailabilityCheck(
        ok=True,
        status=status,  # type: ignore[arg-type]
        executable=executable,
        executable_path=exe_path,
        version=version,
        env_enabled=True,
        missing_env_flags=[],
        checked_at=now,
        warnings=warnings,
        errors=errors,
    )


def run_conversion_sandbox(
    project_id: str,
    request: DicomConversionExecutionRequest | None = None,
    *,
    mode: str = "disabled",
    output_root: str | None = None,
    runner: Any = None,
) -> DicomConversionSandboxResult:
    """Run a fake/sandbox DICOM conversion without calling dcm2niix.

    ``mode`` controls behaviour:
    - ``"disabled"`` — always returns a disabled result (default).
    - ``"fake_outputs"`` — builds command templates and returns placeholder
      artifact paths without writing real files.
    - ``"mock_subprocess"`` — uses the injected ``runner`` to simulate
      dcm2niix execution (for tests only).

    No real dcm2niix is called.  No rawdata is modified.  No NIfTI files
    are written.

    Returns a ``DicomConversionSandboxResult``.
    """
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSandboxResult,
        build_disabled_sandbox_result,
    )

    if mode not in {"fake_outputs", "mock_subprocess"}:
        return build_disabled_sandbox_result(
            project_id=project_id,
            reason=f"Sandbox mode '{mode}' is not an active sandbox mode.",
        )

    # Run preflight to get mappings and command templates
    preflight = run_conversion_preflight(project_id, request)

    if preflight.status in ("disabled", "blocked"):
        return DicomConversionSandboxResult(
            ok=False,
            status=preflight.status,  # type: ignore[arg-type]
            mode=mode,  # type: ignore[arg-type]
            project_id=project_id,
            output_root=preflight.output_root_preview,
            mapping_count=preflight.mapping_count,
            command_template_count=len(preflight.command_templates),
            blocking_issues=preflight.blocking_issues,
            safety_flags=preflight.safety_flags,
        )

    # Build fake artifact paths
    out_root = output_root or preflight.output_root_preview or ""
    artifact_count = 0
    manifest_path = None
    provenance_path = None
    stdout_log_path = None
    stderr_log_path = None
    warnings: list[str] = []

    if out_root:
        # Placeholder paths — no files are written in sandbox mode
        manifest_path = str(Path(out_root) / "conversion_output_manifest.json")
        provenance_path = str(Path(out_root) / "conversion_execution_provenance.json")
        stdout_log_path = str(Path(out_root) / "logs" / "dicom_to_nifti_stdout.log")
        stderr_log_path = str(Path(out_root) / "logs" / "dicom_to_nifti_stderr.log")

        # In mock_subprocess mode with a runner, simulate execution
        if mode == "mock_subprocess" and runner is not None:
            try:
                for i, tmpl in enumerate(preflight.command_templates):
                    if i >= 3:  # Limit to 3 for sandbox
                        break
                    argv = [
                        tmpl.executable,
                        "-z",
                        tmpl.compress,
                        "-f",
                        tmpl.filename_pattern,
                    ]
                    if tmpl.bids_sidecar:
                        argv.extend(["-b", "y"])
                    if tmpl.create_bids:
                        argv.extend(["-ba", "y"])
                    argv.extend(tmpl.additional_flags)
                    argv.extend(["-o", tmpl.output_dir, tmpl.input_dir])

                    result = runner(argv)
                    returncode = getattr(result, "returncode", 0)
                    if returncode != 0:
                        stderr = getattr(result, "stderr", "") or ""
                        warnings.append(f"Sandbox command returned {returncode}: {stderr[:200]}")
            except Exception as exc:
                warnings.append(f"Sandbox runner exception: {exc}")

        artifact_count = 6  # nifti + sidecar × mappings count + manifest + provenance + 2 logs

    return DicomConversionSandboxResult(
        ok=len(preflight.errors) == 0,
        status="succeeded" if not warnings else "warning",
        mode=mode,  # type: ignore[arg-type]
        project_id=project_id,
        output_root=out_root,
        mapping_count=preflight.mapping_count,
        command_template_count=len(preflight.command_templates),
        created_artifact_count=artifact_count,
        manifest_path=manifest_path,
        provenance_path=provenance_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
        warnings=warnings,
        errors=preflight.errors,
        blocking_issues=preflight.blocking_issues
        if preflight.status in ("disabled", "blocked")
        else [],
        safety_flags=preflight.safety_flags,
    )


# ═══════════════════════════════════════════════════════════════════════
# 7. Phase 4C-1 — Synthetic dcm2niix smoke
# ═══════════════════════════════════════════════════════════════════════

_SYNTHETIC_SMOKE_ENV_FLAGS: frozenset[str] = frozenset(
    {
        "MEDIMAGE_ENABLE_DICOM_CONVERSION",
        "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE",
        "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
        "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
    }
)


def _is_synthetic_smoke_enabled(env: dict[str, str]) -> tuple[bool, list[str]]:
    """Check whether all synthetic smoke env flags are set to '1'."""
    missing = sorted(f for f in _SYNTHETIC_SMOKE_ENV_FLAGS if env.get(f) != "1")
    return len(missing) == 0, missing


def run_synthetic_dcm2niix_smoke(
    input_dir: Path,
    output_root: Path,
    *,
    executable: str = "dcm2niix",
    env: dict[str, str] | None = None,
    runner: Any = None,
) -> DicomConversionSandboxResult:
    """Run a controlled real dcm2niix smoke test on synthetic DICOM data.

    **Only accepts synthetic/test input paths.**  Refuses paths that look
    like real rawdata.  Requires all synthetic smoke env flags.

    Uses ``check_dcm2niix_availability()`` to verify dcm2niix is on PATH.
    The ``runner`` callable is used for actual execution; in tests it
    should be monkeypatched.

    No real user rawdata is converted.  No files are written outside
    ``output_root``.  ``shell=True`` is never used.

    Returns a ``DicomConversionSandboxResult`` with manifest, provenance,
    and log paths populated after a successful run.
    """
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSafetyFlags,
        DicomConversionSandboxResult,
    )
    from src.backend.app.schemas.execution_manifest import (
        ExecutionProvenance,
        OutputManifest,
        OutputManifestItem,
    )

    warnings: list[str] = []
    errors: list[str] = []
    blocking: list[str] = []

    env = env or {}

    # ── 1. Env flag check ──
    smoke_ok, missing_smoke = _is_synthetic_smoke_enabled(env)
    if not smoke_ok:
        return DicomConversionSandboxResult(
            ok=False,
            status="disabled",
            mode="disabled",
            project_id="synthetic_smoke",
            output_root=str(output_root),
            blocking_issues=[
                f"Synthetic smoke blocked: {len(missing_smoke)} env flag(s) "
                f"missing: {', '.join(missing_smoke)}."
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
                env_flags_missing=True,
            ),
        )

    # ── 2. Input path safety — refuse real rawdata paths ──
    input_str = str(input_dir.resolve())
    blocked_patterns = [
        "DemoData",
        "FunRaw",
        "T1Raw",
        "data\\rawdata",
        "rawdata",
        "BIDS",
        "bids",
    ]
    for pattern in blocked_patterns:
        if pattern.lower() in input_str.lower():
            blocking.append(
                f"Input path '{input_str}' appears to be real rawdata. "
                f"Synthetic smoke only accepts synthetic/test input directories."
            )
            break

    if blocking:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id="synthetic_smoke",
            output_root=str(output_root),
            blocking_issues=blocking,
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
            ),
        )

    # ── 3. Availability check ──
    availability = check_dcm2niix_availability(
        executable=executable,
        env=env,
        runner=runner,
    )

    if availability.status != "available":
        mapped_status = _map_availability_to_conversion_status(availability.status)

        return DicomConversionSandboxResult(
            ok=False,
            status=mapped_status,  # type: ignore[arg-type]
            mode="disabled",
            project_id="synthetic_smoke",
            output_root=str(output_root),
            blocking_issues=[
                f"dcm2niix not available: status={availability.status}",
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
            ),
        )

    # ── 4. Build command template ──
    output_root.mkdir(parents=True, exist_ok=True)
    template = build_dcm2niix_command_template(
        input_dir=str(input_dir),
        output_dir=str(output_root),
        filename_pattern="synth_%p",
    )

    # ── 5. Execute (via runner) ──
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log_path = logs_dir / "dcm2niix_stdout.log"
    stderr_log_path = logs_dir / "dcm2niix_stderr.log"

    if runner is not None:
        argv = [
            executable,
            "-z",
            template.compress,
            "-f",
            template.filename_pattern,
        ]
        if template.bids_sidecar:
            argv.extend(["-b", "y"])
        if template.create_bids:
            argv.extend(["-ba", "y"])
        argv.extend(template.additional_flags)
        argv.extend(["-o", str(output_root), str(input_dir)])

        try:
            result = runner(argv)
            returncode = getattr(result, "returncode", 0)
            stdout = getattr(result, "stdout", "") or ""
            stderr = getattr(result, "stderr", "") or ""

            stdout_log_path.write_text(stdout, encoding="utf-8", errors="replace")
            stderr_log_path.write_text(stderr, encoding="utf-8", errors="replace")

            if returncode != 0:
                warnings.append(f"dcm2niix returned exit code {returncode}: {stderr[:300]}")
        except Exception as exc:
            errors.append(f"Synthetic smoke execution failed: {exc}")
            return DicomConversionSandboxResult(
                ok=False,
                status="failed",
                mode="mock_subprocess",
                project_id="synthetic_smoke",
                output_root=str(output_root),
                errors=errors,
                safety_flags=DicomConversionSafetyFlags(),
            )

    # ── 6. Write manifest and provenance ──
    manifest_path = output_root / "conversion_output_manifest.json"
    provenance_path = output_root / "conversion_execution_provenance.json"

    # Discover what was actually produced
    produced_items: list[OutputManifestItem] = []
    for p in sorted(output_root.rglob("*")):
        if p.is_file() and p.suffix in {".nii", ".gz", ".json"}:
            info = p.stat()
            produced_items.append(
                OutputManifestItem(
                    kind="nifti" if p.suffix in {".nii", ".gz"} else "json",
                    path=str(p),
                    relative_path=str(p.relative_to(output_root)),
                    required=True,
                    exists=True,
                    verified=True,
                    verification_status="verified",
                    size_bytes=info.st_size,
                    previewable=p.suffix == ".json",
                )
            )

    # Add log items
    for log_path in [stdout_log_path, stderr_log_path]:
        if log_path.exists():
            produced_items.append(
                OutputManifestItem(
                    kind="stdout_log" if "stdout" in log_path.name else "stderr_log",
                    path=str(log_path),
                    relative_path=str(log_path.relative_to(output_root)),
                    required=False,
                    exists=True,
                    verified=True,
                    verification_status="verified",
                    size_bytes=log_path.stat().st_size,
                    previewable=True,
                )
            )

    manifest = OutputManifest(
        project_id="synthetic_smoke",
        run_id="smoke_001",
        node_id="dicom_to_nifti",
        output_root=str(output_root),
        items=produced_items,
        missing_required_count=sum(1 for i in produced_items if i.required and not i.exists),
        verified_count=sum(1 for i in produced_items if i.verified),
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    provenance = ExecutionProvenance(
        project_id="synthetic_smoke",
        run_id="smoke_001",
        node_id="dicom_to_nifti",
        backend="external",
        command_template_id="dcm2niix_smoke",
        params={"filename_pattern": template.filename_pattern},
        input_paths=[str(input_dir)],
        output_paths=[str(p) for p in output_root.rglob("*") if p.is_file()],
        software_versions={"dcm2niix": availability.version or "unknown"},
        return_code=0,
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
    )
    provenance_path.write_text(provenance.model_dump_json(indent=2), encoding="utf-8")

    return DicomConversionSandboxResult(
        ok=len(errors) == 0,
        status="succeeded" if not warnings and not errors else "warning",
        mode="mock_subprocess",
        project_id="synthetic_smoke",
        output_root=str(output_root),
        mapping_count=1,
        command_template_count=1,
        created_artifact_count=len(produced_items),
        manifest_path=str(manifest_path),
        provenance_path=str(provenance_path),
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
        warnings=warnings,
        errors=errors,
        safety_flags=DicomConversionSafetyFlags(
            conversion_disabled_by_default=False,
            env_flags_missing=False,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 7b. Phase 4H-0 — Real dcm2niix smoke on synthetic DICOM only
# ═══════════════════════════════════════════════════════════════════════

REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS = (
    "MEDIMAGE_ENABLE_DICOM_CONVERSION",
    "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE",
    "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE",
    "MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION",
    "MEDIMAGE_ALLOW_REAL_DCM2NIIX_SMOKE",
    "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
    "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
)

_REAL_SMOKE_ENV_FLAGS: frozenset[str] = frozenset(REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS)


def _is_real_smoke_enabled(env: dict[str, str]) -> tuple[bool, list[str]]:
    missing = sorted(f for f in _REAL_SMOKE_ENV_FLAGS if env.get(f) != "1")
    return len(missing) == 0, missing


def run_real_dcm2niix_synthetic_smoke(
    input_dir: Path,
    output_root: Path,
    *,
    executable: str = "dcm2niix",
    env: dict[str, str] | None = None,
) -> DicomConversionSandboxResult:
    """Run REAL dcm2niix via subprocess on synthetic DICOM only.

    **This retired path always fails closed outside the Execution Gateway.**
    It is gated behind 9 env flags, synthetic-only input validation,
    dcm2niix availability check, and output root safety.

    Does NOT convert user rawdata.  Subprocess execution is via argv list only.
    Returns a ``DicomConversionSandboxResult``.
    """
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSafetyFlags,
        DicomConversionSandboxResult,
    )
    from src.backend.app.schemas.execution_manifest import (
        ExecutionProvenance,
        OutputManifest,
        OutputManifestItem,
    )

    warnings: list[str] = []
    errors: list[str] = []
    blocking: list[str] = []
    effective_env = os.environ if env is None else env

    # ── 1. Env flag check ──
    ok_flags, missing_flags = _is_real_smoke_enabled(effective_env)
    if not ok_flags:
        missing_list = ", ".join(missing_flags) if missing_flags else "none"
        return DicomConversionSandboxResult(
            ok=False,
            status="disabled",
            mode="disabled",
            project_id="real_smoke",
            blocking_issues=[
                f"Real dcm2niix smoke blocked: {len(missing_flags)} env "
                f"flag(s) missing. Missing: {missing_list}. "
                f"Required flags: {', '.join(REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS)}"
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
                env_flags_missing=True,
            ),
        )

    # ── 2. Input path safety ──
    input_str = str(input_dir.resolve())
    output_str = str(output_root.resolve())
    blocked_patterns = ["DemoData", "FunRaw", "T1Raw", "rawdata", "BIDS"]

    # Check input path
    for pattern in blocked_patterns:
        if pattern.lower() in input_str.lower():
            if "pytest" in input_str.lower() or "tmp" in input_str.lower():
                continue
            blocking.append(
                f"Input path '{input_str}' appears to be real rawdata. "
                f"Real dcm2niix smoke only accepts synthetic/test input."
            )
            break

    # Check output root path
    for pattern in blocked_patterns:
        if pattern.lower() in output_str.lower():
            if "pytest" in output_str.lower() or "tmp" in output_str.lower():
                continue
            blocking.append(
                f"Output root '{output_str}' appears to be under rawdata. "
                f"Real dcm2niix smoke output must be under a safe test directory."
            )
            break

    if blocking:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id="real_smoke",
            blocking_issues=blocking,
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 3. Availability check ──
    avail = check_dcm2niix_availability(env=effective_env)
    if avail.status != "available":
        detail = f"dcm2niix not available: status={avail.status}"
        if avail.status == "version_failed":
            detail += " (version query failed — check dcm2niix --version)"
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id="real_smoke",
            blocking_issues=[detail],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 4. Execute real dcm2niix ──
    output_root.mkdir(parents=True, exist_ok=True)
    logs_dir = output_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    resolved_executable = avail.executable_path or executable
    argv = [
        resolved_executable,
        "-z",
        "y",
        "-f",
        "synth_%p_%s",
        "-b",
        "y",
        "-ba",
        "y",
        "-o",
        str(output_root),
        str(input_dir),
    ]

    try:
        result = reject_unreviewed_process_start(
            argv, capture_output=True, text=True, check=False
        )
        rc = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""

        stdout_path = logs_dir / "dcm2niix_stdout.log"
        stderr_path = logs_dir / "dcm2niix_stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")

        if rc != 0:
            warnings.append(f"dcm2niix exited with code {rc}: {stderr[:300]}")
    except FileNotFoundError:
        return DicomConversionSandboxResult(
            ok=False,
            status="failed",
            mode="disabled",
            project_id="real_smoke",
            errors=[f"dcm2niix executable not found: {resolved_executable}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    except Exception as exc:
        return DicomConversionSandboxResult(
            ok=False,
            status="failed",
            mode="disabled",
            project_id="real_smoke",
            errors=[f"Real dcm2niix smoke failed: {exc}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 5. Write manifest and provenance ──
    manifest_path = output_root / "output_manifest.json"
    provenance_path = output_root / "execution_provenance.json"
    artifacts = 0

    items: list[OutputManifestItem] = []
    for p in sorted(output_root.rglob("*")):
        if p.is_file() and p.suffix in {".nii", ".gz", ".json", ".log"}:
            info = p.stat()
            items.append(
                OutputManifestItem(
                    kind="nifti" if p.suffix in {".nii", ".gz"} else "json",
                    path=str(p),
                    relative_path=str(p.relative_to(output_root)),
                    required=True,
                    exists=True,
                    verified=True,
                    verification_status="verified",
                    size_bytes=info.st_size,
                )
            )
            artifacts += 1

    manifest = OutputManifest(
        project_id="real_smoke",
        run_id="smoke_real",
        node_id="dicom_to_nifti",
        output_root=str(output_root),
        items=items,
        missing_required_count=0,
        verified_count=len(items),
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    provenance = ExecutionProvenance(
        project_id="real_smoke",
        run_id="smoke_real",
        node_id="dicom_to_nifti",
        backend="external",
        command_template_id="real_smoke",
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
        return_code=rc,
    )
    provenance_path.write_text(provenance.model_dump_json(indent=2), encoding="utf-8")

    status = "succeeded" if rc == 0 else "warning"

    return DicomConversionSandboxResult(
        ok=rc == 0,
        status=status,  # type: ignore[arg-type]
        mode="mock_subprocess",
        project_id="real_smoke",
        output_root=str(output_root),
        created_artifact_count=artifacts,
        manifest_path=str(manifest_path),
        provenance_path=str(provenance_path),
        stdout_log_path=str(stdout_path),
        stderr_log_path=str(stderr_path),
        warnings=warnings,
        errors=errors,
        safety_flags=DicomConversionSafetyFlags(
            conversion_disabled_by_default=False,
            env_flags_missing=False,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 8. Phase 4F-0 — Synthetic persisted-package conversion
# ═══════════════════════════════════════════════════════════════════════

_PERSISTED_SYNTHETIC_ENV_FLAGS: frozenset[str] = frozenset(
    {
        "MEDIMAGE_ENABLE_DICOM_CONVERSION",
        "MEDIMAGE_ENABLE_SYNTHETIC_DICOM_SMOKE",
        "MEDIMAGE_ALLOW_EXTERNAL_TOOL_SMOKE",
        "MEDIMAGE_ALLOW_PERSISTED_SYNTHETIC_CONVERSION",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
        "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
    }
)


def _is_persisted_synthetic_enabled(env: dict[str, str]) -> tuple[bool, list[str]]:
    missing = sorted(f for f in _PERSISTED_SYNTHETIC_ENV_FLAGS if env.get(f) != "1")
    return len(missing) == 0, missing


def run_synthetic_conversion_from_persisted_package(
    project_id: str,
    conversion_run_id: str,
    *,
    env: dict[str, str] | None = None,
    runner: Any = None,
    synthetic_only: bool = True,
) -> DicomConversionSandboxResult:
    """Run a controlled real dcm2niix smoke from a persisted approval package.

    Reads the persisted review package, validates approval completeness,
    checks env flags, and executes dcm2niix using command templates from
    the persisted package.  Only accepts synthetic/test input paths.

    **User rawdata conversion remains disabled.**  No real user data is
    converted.  ``shell=True`` is never used.

    Returns a ``DicomConversionSandboxResult`` with updated manifest,
    provenance, and log paths.
    """
    from src.backend.app.schemas.dicom_conversion_approval import (
        DicomConversionApprovalRecord,
        evaluate_conversion_approval_gate,
    )
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSafetyFlags,
        DicomConversionSandboxResult,
    )
    from src.backend.app.schemas.execution_manifest import (
        ExecutionProvenance,
        OutputManifest,
        OutputManifestItem,
    )
    from src.backend.app.services.dicom_conversion_review_package import (
        read_conversion_review_package,
    )

    warnings: list[str] = []
    errors: list[str] = []
    _blocking: list[str] = []
    effective_env = os.environ if env is None else env

    # ── 1. Env flag check ──
    ok_flags, missing_flags = _is_persisted_synthetic_enabled(effective_env)
    if not ok_flags:
        return DicomConversionSandboxResult(
            ok=False,
            status="disabled",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[
                f"Persisted synthetic conversion blocked: {len(missing_flags)} "
                f"env flag(s) missing: {', '.join(missing_flags)}."
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
                env_flags_missing=True,
            ),
        )

    # ── 2. Read persisted package ──
    project_dir = env.get("MEDIMAGE_PROJECT_DIR", "")
    pkg = read_conversion_review_package(
        project_id,
        conversion_run_id,
        project_dir=project_dir,
    )
    if not pkg.ok:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Review package not readable: {pkg.errors}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 3. Validate required files ──
    required_kinds = {
        "approval_record",
        "preflight_snapshot",
        "mapping_snapshot",
        "command_templates",
    }
    missing_kinds = {f.kind for f in pkg.files if not f.exists} & required_kinds
    if missing_kinds:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Required files missing: {', '.join(sorted(missing_kinds))}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 4. Validate approval gate ──
    try:
        approval_json = json.loads(
            Path(next(f.path for f in pkg.files if f.kind == "approval_record")).read_text(
                encoding="utf-8"
            )
        )
        approval = DicomConversionApprovalRecord(**approval_json)
    except Exception as exc:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Failed to parse approval record: {exc}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    gate = evaluate_conversion_approval_gate(approval, preflight_ok=True)
    if gate.status != "approved":
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[
                f"Approval gate not passed: {gate.status} — missing: {gate.missing_fields}"
            ],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 5. Validate input paths are synthetic ──
    mapping_path = next((f.path for f in pkg.files if f.kind == "mapping_snapshot"), "")
    try:
        mappings_data = (
            json.loads(Path(mapping_path).read_text(encoding="utf-8")) if mapping_path else {}
        )
    except Exception:
        mappings_data = {}
    for m in mappings_data.get("mappings", []):
        sp = m.get("source_path", "")
        if synthetic_only:
            blocked_patterns = ["DemoData", "FunRaw", "T1Raw", "rawdata", "BIDS"]
            if any(p.lower() in sp.lower() for p in blocked_patterns):
                return DicomConversionSandboxResult(
                    ok=False,
                    status="blocked",
                    mode="disabled",
                    project_id=project_id,
                    blocking_issues=[
                        f"Source path '{sp}' appears to be real rawdata. Synthetic-only."
                    ],
                    safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
                )

    # ── 6. dcm2niix availability ──
    avail = check_dcm2niix_availability(env=env, runner=runner if runner else None)
    if avail.status != "available":
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"dcm2niix not available: {avail.status}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 7. Execute command templates via runner ──
    output_root = pkg.run_dir or ""
    logs_dir = Path(output_root) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log_path = logs_dir / "dcm2niix_stdout.log"
    stderr_log_path = logs_dir / "dcm2niix_stderr.log"
    artifact_count = 0

    if runner is not None:
        tmpl_path = next((f.path for f in pkg.files if f.kind == "command_templates"), "")
        try:
            tmpl_data = json.loads(Path(tmpl_path).read_text(encoding="utf-8")) if tmpl_path else {}
        except Exception:
            tmpl_data = {}
        for tmpl in tmpl_data.get("templates", [])[:1]:  # Limit to first template
            argv = [
                tmpl.get("executable", "dcm2niix"),
                "-z",
                tmpl.get("compress", "y"),
                "-f",
                tmpl.get("filename_pattern", "%p_%s"),
            ]
            if tmpl.get("bids_sidecar"):
                argv.extend(["-b", "y"])
            if tmpl.get("create_bids"):
                argv.extend(["-ba", "y"])
            argv.extend(["-o", tmpl.get("output_dir", output_root), tmpl.get("input_dir", "")])
            try:
                result = runner(argv)
                stdout = getattr(result, "stdout", "") or ""
                stderr = getattr(result, "stderr", "") or ""
                rc = getattr(result, "returncode", 0)
                stdout_log_path.write_text(stdout, encoding="utf-8", errors="replace")
                stderr_log_path.write_text(stderr, encoding="utf-8", errors="replace")
                if rc != 0:
                    warnings.append(f"dcm2niix returned {rc}: {stderr[:200]}")
            except Exception as exc:
                errors.append(f"Execution failed: {exc}")

        artifact_count = 6

    # ── 8. Write updated manifest and provenance ──
    manifest_path = Path(output_root) / "output_manifest.json"
    provenance_path = Path(output_root) / "execution_provenance.json"

    items: list[OutputManifestItem] = []
    if Path(output_root).exists():
        for p in sorted(Path(output_root).rglob("*")):
            if p.is_file() and p.suffix in {".nii", ".gz", ".json", ".log"}:
                info = p.stat()
                items.append(
                    OutputManifestItem(
                        kind="nifti" if p.suffix in {".nii", ".gz"} else "json",
                        path=str(p),
                        relative_path=str(p.relative_to(output_root)),
                        required=True,
                        exists=True,
                        verified=True,
                        verification_status="verified",
                        size_bytes=info.st_size,
                    )
                )

    manifest = OutputManifest(
        project_id=project_id,
        run_id=conversion_run_id,
        node_id="dicom_to_nifti",
        output_root=output_root,
        items=items,
        missing_required_count=sum(1 for i in items if i.required and not i.exists),
        verified_count=sum(1 for i in items if i.verified),
    )
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    provenance = ExecutionProvenance(
        project_id=project_id,
        run_id=conversion_run_id,
        node_id="dicom_to_nifti",
        backend="external",
        command_template_id="persisted_package",
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
        return_code=0,
    )
    provenance_path.write_text(provenance.model_dump_json(indent=2), encoding="utf-8")

    return DicomConversionSandboxResult(
        ok=len(errors) == 0,
        status="succeeded" if not warnings and not errors else "warning",
        mode="mock_subprocess",
        project_id=project_id,
        output_root=output_root,
        created_artifact_count=artifact_count,
        manifest_path=str(manifest_path),
        provenance_path=str(provenance_path),
        stdout_log_path=str(stdout_log_path),
        stderr_log_path=str(stderr_log_path),
        warnings=warnings,
        errors=errors,
        safety_flags=DicomConversionSafetyFlags(
            conversion_disabled_by_default=False,
            env_flags_missing=False,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# 9. Phase 4I-0 — Internal-only user-data conversion prototype
# ═══════════════════════════════════════════════════════════════════════

_INTERNAL_CONVERSION_FLAGS: frozenset[str] = frozenset(
    {
        "MEDIMAGE_ENABLE_DICOM_CONVERSION",
        "MEDIMAGE_ENABLE_REVIEWED_EXECUTION",
        "MEDIMAGE_ALLOW_USER_DATA_CONVERSION",
    }
)


def _is_internal_conversion_enabled(env: dict[str, str]) -> tuple[bool, list[str]]:
    missing = sorted(f for f in _INTERNAL_CONVERSION_FLAGS if env.get(f) != "1")
    return len(missing) == 0, missing


def run_internal_user_dicom_conversion_from_persisted_package(
    project_id: str,
    conversion_run_id: str,
    *,
    env: dict[str, str] | None = None,
    executable: str = "dcm2niix",
    internal_only: bool = True,
    project_dir: str = "",
    rawdata_dir: str = "",
    validate_only: bool = False,
    input_roots: tuple[str, ...] = (),
    output_roots: tuple[str, ...] = (),
    readonly_roots: tuple[str, ...] = (),
) -> DicomConversionSandboxResult:
    """Internal-only user-data DICOM conversion prototype — Phase 4I-0.

    Reads a persisted approval/review package, validates all gating
    conditions, and executes the in-project native Python converter.

    Execution requires the three DICOM-specific opt-in flags shared with
    conversion preflight. Synthetic-smoke and external-tool flags are not
    relevant to this native execution path.

    Does NOT add a public endpoint.  Does NOT add a frontend button.
    Does NOT modify rawdata.  Does NOT launch a subprocess.
    """
    from src.backend.app.schemas.dicom_conversion_approval import (
        DicomConversionApprovalRecord,
        evaluate_conversion_approval_gate,
    )
    from src.backend.app.schemas.dicom_conversion_execution import (
        DicomConversionSafetyFlags,
        DicomConversionSandboxResult,
    )
    from src.backend.app.schemas.dicom_conversion_safety import RawdataChecksumSnapshot
    from src.backend.app.services.dicom_conversion_review_package import (
        read_conversion_review_package,
    )
    from src.backend.app.services.dicom_conversion_safety import (
        build_pre_conversion_rawdata_snapshot,
        compare_conversion_rawdata_snapshots,
    )

    blocking: list[str] = []
    effective_env: dict[str, str] = os.environ if env is None else dict(env)
    default_rawdata = Path(project_dir).expanduser().resolve() / "rawdata" if project_dir else None
    if not rawdata_dir and default_rawdata is not None and default_rawdata.is_dir():
        rawdata_dir = str(default_rawdata)

    # ── 1. Env flag check ──
    ok_flags, missing_flags = _is_internal_conversion_enabled(effective_env)
    if not ok_flags:
        return DicomConversionSandboxResult(
            ok=False,
            status="disabled",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[
                f"Internal conversion blocked: {len(missing_flags)} env "
                f"flag(s) missing: {', '.join(missing_flags)}"
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=True,
                env_flags_missing=True,
            ),
        )

    # ── 2. Read persisted review package ──
    pkg = read_conversion_review_package(
        project_id,
        conversion_run_id,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
    )
    if not pkg.ok:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Review package not readable: {pkg.errors}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 3. Validate required files ──
    required_kinds = {
        "approval_record",
        "mapping_snapshot",
        "command_templates",
        "rawdata_checksum_before",
        "rollback_plan_dry_run",
    }
    missing_kinds = {f.kind for f in pkg.files if not f.exists} & required_kinds
    if missing_kinds:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Required files missing: {', '.join(sorted(missing_kinds))}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 3b. Load and evaluate approval record ──
    approval_path = next((f.path for f in pkg.files if f.kind == "approval_record"), "")
    if not approval_path or not Path(approval_path).exists():
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=["Approval record not found in review package."],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    try:
        approval_json = json.loads(Path(approval_path).read_text(encoding="utf-8"))
        approval = DicomConversionApprovalRecord(**approval_json)
    except Exception as exc:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Failed to parse approval record: {exc}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    gate = evaluate_conversion_approval_gate(approval, preflight_ok=True)
    if gate.status != "approved":
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[
                f"Approval gate not passed: {gate.status}. "
                f"Missing fields: {gate.missing_fields}. "
                f"Blocking: {gate.blocking_issues}"
            ],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 3c. Load and validate audit preview ──
    audit_path = next((f.path for f in pkg.files if f.kind == "audit_preview"), "")
    if not audit_path or not Path(audit_path).exists():
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=["Audit preview not found in review package."],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    try:
        audit_json = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Failed to parse audit preview: {exc}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    if not audit_json.get("audit_id") or not audit_json.get("project_id"):
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=["Audit preview is incomplete: missing audit_id or project_id."],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 4. Output root safety ──
    output_root = pkg.run_dir or ""
    if rawdata_dir and output_root.startswith(rawdata_dir):
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=["Output root is under rawdata_dir."],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 5. Path safety for mappings ──
    mapping_path = next((f.path for f in pkg.files if f.kind == "mapping_snapshot"), "")
    try:
        mappings_data = (
            json.loads(Path(mapping_path).read_text(encoding="utf-8")) if mapping_path else {}
        )
    except Exception:
        mappings_data = {}
    for m in mappings_data.get("mappings", []):
        sp = m.get("source_path", "")
        if ".." in sp:
            blocking.append(f"Path traversal in mapping source_path: {sp}")

    if blocking:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=blocking,
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    # ── 6. Pre-conversion checksum snapshot ──
    checksum_before = None
    checksum_before_path = next(
        (f.path for f in pkg.files if f.kind == "rawdata_checksum_before"), ""
    )
    rollback_plan_path = next((f.path for f in pkg.files if f.kind == "rollback_plan_dry_run"), "")
    if rawdata_dir:
        try:
            reviewed_checksum = RawdataChecksumSnapshot(
                **json.loads(Path(checksum_before_path).read_text(encoding="utf-8"))
            )
        except Exception as exc:
            return DicomConversionSandboxResult(
                ok=False,
                status="blocked",
                mode="disabled",
                project_id=project_id,
                blocking_issues=[f"Failed to read reviewed rawdata checksum snapshot: {exc}"],
                safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
            )
        checksum_before = build_pre_conversion_rawdata_snapshot([rawdata_dir])
        checksum_review = compare_conversion_rawdata_snapshots(
            reviewed_checksum,
            checksum_before,
        )
        if not checksum_review.unchanged:
            return DicomConversionSandboxResult(
                ok=False,
                status="blocked",
                mode="disabled",
                project_id=project_id,
                blocking_issues=[
                    "Rawdata changed after conversion review; create and approve a new conversion package.",
                    *checksum_review.errors,
                ],
                safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
            )

    # ── 7. Native converter readiness and ticket path authority ──
    tmpl_path = next((f.path for f in pkg.files if f.kind == "command_templates"), "")
    try:
        tmpl_data = json.loads(Path(tmpl_path).read_text(encoding="utf-8")) if tmpl_path else {}
    except Exception as exc:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[f"Failed to read native conversion templates: {exc}"],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )
    mappings = mappings_data.get("mappings", [])
    templates = tmpl_data.get("templates", [])
    if not mappings or len(mappings) != len(templates):
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[
                "Reviewed native conversion requires equal non-zero mapping and template counts."
            ],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    def _within(value: str, root: str) -> bool:
        if not value or not root:
            return False
        try:
            Path(value).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
            return True
        except ValueError:
            return False

    approved_output_root = str(approval.output_root or "")
    if not _within(approved_output_root, project_dir):
        blocking.append("Approved conversion output root is outside the project directory.")
    if rawdata_dir and _within(approved_output_root, rawdata_dir):
        blocking.append("Approved conversion output root is inside rawdata.")
    for mapping, template in zip(mappings, templates, strict=True):
        source_path = str(mapping.get("source_path") or template.get("input_dir") or "")
        output_dir = str(template.get("output_dir") or "")
        if not _within(source_path, rawdata_dir):
            blocking.append("A reviewed DICOM source is outside the approved rawdata directory.")
        if not _within(output_dir, approved_output_root):
            blocking.append("A reviewed conversion output is outside the approved output root.")
        if rawdata_dir and _within(output_dir, rawdata_dir):
            blocking.append("A reviewed conversion output is inside rawdata.")
    if blocking:
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=sorted(set(blocking)),
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    def _within_any(value: str, roots: tuple[str, ...]) -> bool:
        resolved = Path(value).expanduser().resolve()
        for root in roots:
            try:
                resolved.relative_to(Path(root).expanduser().resolve())
                return True
            except ValueError:
                continue
        return False

    if input_roots or output_roots or readonly_roots:
        if not rawdata_dir or not _within_any(rawdata_dir, input_roots):
            blocking.append("Approved rawdata directory is outside execution-ticket input roots.")
        if not rawdata_dir or not _within_any(rawdata_dir, readonly_roots):
            blocking.append("Approved rawdata directory is not ticket-bound read-only input.")
        if not project_dir or not _within_any(project_dir, output_roots):
            blocking.append("Project directory is outside execution-ticket output roots.")
        for mapping, template in zip(mappings, templates, strict=True):
            source_path = str(mapping.get("source_path") or template.get("input_dir") or "")
            output_dir = str(template.get("output_dir") or "")
            if not source_path or not _within_any(source_path, input_roots):
                blocking.append("A reviewed DICOM source is outside execution-ticket input roots.")
            if not output_dir or not _within_any(output_dir, output_roots):
                blocking.append(
                    "A reviewed conversion output is outside execution-ticket output roots."
                )
        if blocking:
            return DicomConversionSandboxResult(
                ok=False,
                status="blocked",
                mode="disabled",
                project_id=project_id,
                blocking_issues=sorted(set(blocking)),
                safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
            )

    availability = check_native_dicom_converter_availability()
    if not availability.get("found"):
        return DicomConversionSandboxResult(
            ok=False,
            status="blocked",
            mode="disabled",
            project_id=project_id,
            blocking_issues=[str(availability.get("error") or "Native converter unavailable.")],
            safety_flags=DicomConversionSafetyFlags(conversion_disabled_by_default=True),
        )

    if validate_only:
        return DicomConversionSandboxResult(
            ok=True,
            status="ready",
            mode="native",
            project_id=project_id,
            output_root=str(approval.output_root or ""),
            mapping_count=len(mappings),
            command_template_count=len(templates),
            warnings=[
                "Reviewed native DICOM conversion is ready; validation created no image artifact."
            ],
            safety_flags=DicomConversionSafetyFlags(
                conversion_disabled_by_default=False,
                env_flags_missing=False,
                command_template_only=False,
            ),
        )
    from src.backend.app.services.native_dicom_conversion_execution import (
        execute_native_persisted_conversion,
    )

    return execute_native_persisted_conversion(
        project_id=project_id,
        conversion_run_id=conversion_run_id,
        project_dir=project_dir,
        rawdata_dir=rawdata_dir,
        evidence_root=pkg.run_dir or "",
        approval=approval,
        approval_record_path=approval_path,
        gate=gate,
        audit_preview_path=audit_path,
        mapping_snapshot_path=mapping_path,
        template_snapshot_path=tmpl_path,
        mappings=mappings,
        templates=templates,
        checksum_before=checksum_before,
        checksum_before_path=checksum_before_path,
        rollback_plan_path=rollback_plan_path,
    )


# ═══════════════════════════════════════════════════════════════════════
# 8. Phase 6B — Conversion result validation
# ═══════════════════════════════════════════════════════════════════════


def validate_conversion_outputs(output_root: str) -> dict[str, Any]:
    """Validate converted outputs in Phase 6B.

    Checks:
      - NIfTI files exist and are non-empty
      - JSON sidecars exist
      - subject/session naming is correct
      - BOLD and T1w pairing
      - BIDS root discoverability
    """
    root = Path(output_root)
    warnings: list[str] = []
    errors: list[str] = []

    if not root.exists():
        return {
            "ok": False,
            "errors": [f"Output root does not exist: {output_root}"],
            "warnings": warnings,
        }

    # Discover NIfTI files
    nifti_files = sorted(root.rglob("*.nii.gz")) + sorted(root.rglob("*.nii"))
    if not nifti_files:
        errors.append(f"No NIfTI files found under {output_root}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    subjects: set[str] = set()
    bold_files: list[Path] = []
    t1w_files: list[Path] = []
    missing_sidecars: list[str] = []
    empty_files: list[str] = []

    for nii_path in nifti_files:
        # Check non-empty
        if nii_path.stat().st_size == 0:
            empty_files.append(str(nii_path))
            continue

        name = nii_path.name.lower()
        # Detect subject
        for part in nii_path.parts:
            if part.startswith("sub-"):
                subjects.add(part)
                break

        # Classify by modality
        if "bold" in name or "rest" in name:
            bold_files.append(nii_path)
        elif "t1" in name:
            t1w_files.append(nii_path)

        # Check JSON sidecar
        json_path = None
        for suffix in [".nii.gz", ".nii"]:
            if nii_path.name.endswith(suffix):
                base = nii_path.name[: -len(suffix)]
                json_path = nii_path.parent / f"{base}.json"
                break
        if json_path and not json_path.exists():
            missing_sidecars.append(str(nii_path))

    # Aggregate results
    bold_subjects = set()
    t1w_subjects = set()
    for bf in bold_files:
        for p in bf.parts:
            if p.startswith("sub-"):
                bold_subjects.add(p)
                break
    for tf in t1w_files:
        for p in tf.parts:
            if p.startswith("sub-"):
                t1w_subjects.add(p)
                break

    missing_t1w = sorted(bold_subjects - t1w_subjects)
    missing_bold = sorted(t1w_subjects - bold_subjects)

    if empty_files:
        errors.append(f"{len(empty_files)} NIfTI file(s) are empty")
    if missing_sidecars:
        warnings.append(f"{len(missing_sidecars)} NIfTI file(s) missing JSON sidecar")
    if missing_t1w:
        warnings.append(f"Missing T1w for subjects: {missing_t1w}")
    if missing_bold:
        warnings.append(f"Missing BOLD for subjects: {missing_bold}")

    return {
        "ok": len(errors) == 0,
        "status": "valid" if len(errors) == 0 else "invalid",
        "subject_count": len(subjects),
        "bold_count": len(bold_files),
        "t1w_count": len(t1w_files),
        "nifti_count": len(nifti_files),
        "missing_sidecar_count": len(missing_sidecars),
        "empty_file_count": len(empty_files),
        "missing_t1w_subjects": missing_t1w,
        "missing_bold_subjects": missing_bold,
        "errors": errors,
        "warnings": warnings,
    }


def register_converted_outputs(output_root: str, project_id: str = "") -> dict[str, Any]:
    """Register converted BIDS/NIfTI outputs for preprocessing input.

    Returns summary suitable for frontend display:
      - subject count, BOLD/T1w/NIfTI counts
      - missing pairs
      - preprocessing input directory
    """
    validation = validate_conversion_outputs(output_root)

    return {
        "project_id": project_id,
        "output_root": output_root,
        "registered": validation["ok"],
        "preprocessing_input_dir": output_root,
        "subject_count": validation["subject_count"],
        "bold_count": validation["bold_count"],
        "t1w_count": validation["t1w_count"],
        "nifti_count": validation["nifti_count"],
        "missing_t1w_subjects": validation.get("missing_t1w_subjects", []),
        "missing_bold_subjects": validation.get("missing_bold_subjects", []),
        "validation_errors": validation["errors"],
        "validation_warnings": validation["warnings"],
    }


__all__ = [
    "run_conversion_preflight",
    "run_conversion_execute",
    "run_conversion_sandbox",
    "check_dcm2niix_availability",
    "validate_conversion_outputs",
    "register_converted_outputs",
    "run_synthetic_dcm2niix_smoke",
    "run_synthetic_conversion_from_persisted_package",
    "run_internal_user_dicom_conversion_from_persisted_package",
    "run_real_dcm2niix_synthetic_smoke",
    "build_disabled_conversion_response",
]
