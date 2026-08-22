"""MATLAB / SPM environment health detection.

Reads desktop config for MATLAB and SPM paths, checks existence,
optionally queries versions.  Never runs preprocessing or realignment.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from src.backend.app.runtime.desktop_config import get_desktop_config
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _matlab_command(config: dict[str, Any]) -> str:
    return str(config.get("matlab_command", "matlab"))


def _spm_path(config: dict[str, Any]) -> str:
    return str(config.get("spm_dir", "./third_party/spm12"))


def _exists_and_type(path_str: str) -> tuple[bool, str | None]:
    """Check whether a path exists and is a directory or file."""
    try:
        p = Path(path_str).expanduser().resolve()
        if not p.exists():
            return False, None
        if p.is_dir():
            return True, "directory"
        if p.is_file():
            return True, "file"
        return True, "other"
    except Exception:
        return False, None


def _try_matlab_version(
    command: str, timeout_seconds: int = 5
) -> tuple[str | None, list[str], list[str]]:
    """Try to get MATLAB version via -batch.  Returns (version, warnings, errors)."""
    warnings: list[str] = []
    errors: list[str] = []
    try:
        result = reject_unreviewed_process_start(
            [command, "-batch", "disp(version); exit"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output:
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                version = lines[-1] if lines else None
                return version, warnings, errors
            else:
                errors.append("MATLAB version query returned empty output.")
                return None, warnings, errors
        else:
            errors.append(
                f"MATLAB version query failed with return code {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            )
            return None, warnings, errors
    except subprocess.TimeoutExpired:
        warnings.append(f"MATLAB version query timed out after {timeout_seconds}s.")
        return None, warnings, errors
    except FileNotFoundError:
        errors.append(f"MATLAB command not found: {command}")
        return None, warnings, errors
    except Exception as exc:
        errors.append(f"MATLAB version query error: {exc}")
        return None, warnings, errors


def build_matlab_spm_health() -> dict[str, Any]:
    """Build MATLAB/SPM health report from desktop config.

    Never runs realign or any preprocessing.  Version queries are
    only attempted if the executable exists and the project has
    an explicit env flag for MATLAB health checks, OR if the
    executable is found via shutil.which.
    """
    config = get_desktop_config()
    matlab_cmd = _matlab_command(config)
    spm_dir = _spm_path(config)

    # ── MATLAB ──
    matlab_found = shutil.which(matlab_cmd)
    matlab_path_exists = Path(matlab_cmd).exists()
    matlab_configured = bool(config.get("matlab_command"))

    matlab_version: str | None = None
    version_attempted = False
    version_ok = False
    version_warnings: list[str] = []
    version_errors: list[str] = []

    if matlab_found or matlab_path_exists:
        effective_cmd = matlab_found or matlab_cmd
        version_attempted = True
        matlab_version, version_warnings, version_errors = _try_matlab_version(effective_cmd)

    if matlab_version:
        version_ok = True

    matlab_health: dict[str, Any] = {
        "configured": matlab_configured,
        "executable_path": matlab_cmd,
        "exists": bool(matlab_found or matlab_path_exists),
        "version": matlab_version,
        "version_check_attempted": version_attempted,
        "version_check_ok": version_ok,
        "warnings": version_warnings,
        "errors": version_errors,
    }

    # ── SPM ──
    spm_exists, spm_path_type = _exists_and_type(spm_dir)
    spm_configured = bool(config.get("spm_dir"))
    spm_version: str | None = None
    spm_version_attempted = False
    spm_version_ok = False
    spm_warnings: list[str] = []
    spm_errors: list[str] = []

    # Try to detect SPM version from Contents.m or spm.m
    if spm_exists and spm_path_type == "directory":
        spm_version_attempted = True
        try:
            contents_path = Path(spm_dir).expanduser().resolve() / "Contents.m"
            if contents_path.is_file():
                text = contents_path.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if "Version" in line or "version" in line or "SPM" in line:
                        candidate = line.strip().strip("%").strip()
                        if candidate:
                            spm_version = candidate[:120]
                            break
                if spm_version:
                    spm_version_ok = True
                else:
                    spm_warnings.append("SPM version could not be determined from Contents.m.")
            else:
                spm_warnings.append("SPM directory exists but Contents.m not found.")
        except Exception as exc:
            spm_errors.append(f"SPM version detection error: {exc}")

    spm_health: dict[str, Any] = {
        "configured": spm_configured,
        "spm_path": spm_dir,
        "exists": spm_exists,
        "version": spm_version,
        "version_check_attempted": spm_version_attempted,
        "version_check_ok": spm_version_ok,
        "warnings": spm_warnings,
        "errors": spm_errors,
    }

    # ── Overall execution readiness ──
    env_flag = config.get("MEDIMAGE_MATLAB_ENABLED") or False
    real_execution_enabled = bool(env_flag)

    # Determine overall status
    if not matlab_configured and not spm_configured:
        status = "not_configured"
    elif not matlab_health["exists"] or not spm_health["exists"]:
        status = "warning"
    elif version_ok or spm_version_ok:
        status = "ready_for_dry_run_check"
    else:
        status = "disabled"

    notes: list[str] = []
    if not real_execution_enabled:
        notes.append("MATLAB/SPM real execution is NOT enabled via environment flag.")
    notes.append("spm_realign_subject is metadata-only and not currently safe-allowlisted.")
    notes.append("Approval Gate and audit are still required for future execution.")

    return {
        "status": status,
        "matlab": matlab_health,
        "spm": spm_health,
        "real_execution_enabled": real_execution_enabled,
        "safe_allowlist_enabled": False,
        "notes": notes,
    }
