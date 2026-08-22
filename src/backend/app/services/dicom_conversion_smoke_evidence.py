"""Synthetic dcm2niix smoke evidence capture — Phase 4H-3.

Captures structured evidence from a real dcm2niix synthetic smoke run
for GO/NO-GO review.  Only runs when all 9 env flags are set AND
dcm2niix AND pydicom are available.

Does NOT touch real user rawdata.  Does NOT modify rawdata.
Does NOT use shell=True.

Usage:
    Set all 9 MEDIMAGE_* flags to "1", then run:
    python -c "from src.backend.app.services.dicom_conversion_smoke_evidence import capture_synthetic_smoke_evidence; print(capture_synthetic_smoke_evidence())"
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


from src.backend.app.services.dicom_conversion_execution import (  # noqa: E402
    REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS,
)


def _all_flags_present() -> bool:
    return all(os.environ.get(f) == "1" for f in REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS)


def _dcm2niix_available() -> bool:
    return shutil.which("dcm2niix") is not None


def _pydicom_available() -> bool:
    try:
        import pydicom  # noqa: F401

        return True
    except ImportError:
        return False


def capture_synthetic_smoke_evidence() -> dict[str, Any]:
    """Run a real dcm2niix synthetic smoke and capture structured evidence.

    Returns a dict suitable for GO/NO-GO review evidence recording.
    Returns ``{"status": "skipped", ...}`` if prerequisites are not met.
    """
    if not _all_flags_present():
        return {
            "status": "skipped",
            "reason": "Required env flags not all set to '1'.",
            "required_flags": list(REAL_DCM2NIIX_SYNTHETIC_SMOKE_REQUIRED_FLAGS),
        }

    if not _dcm2niix_available():
        return {
            "status": "skipped",
            "reason": "dcm2niix not found on system PATH.",
        }

    if not _pydicom_available():
        return {
            "status": "skipped",
            "reason": "pydicom not installed.",
        }

    # All gates passed — run real smoke
    from tests.unit.dicom_synthetic_helpers import create_minimal_dicom_series

    evidence: dict[str, Any] = {
        "status": "running",
        "started_at": _now_iso(),
    }

    try:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = create_minimal_dicom_series(
                tmp_path,
                subject_id="sub-smoke",
                num_slices=5,
            )
            output_root = tmp_path / "output"
            output_root.mkdir()

            evidence["input_dir"] = str(input_dir)
            evidence["output_root"] = str(output_root)

            # Capture dcm2niix version
            ver_result = reject_unreviewed_process_start(
                ["dcm2niix", "--version"],
                capture_output=True,
                text=True,
            )
            evidence["dcm2niix_version"] = (
                ver_result.stdout.strip().split("\n")[0] if ver_result.stdout else "unknown"
            )

            # Run conversion
            logs_dir = output_root / "logs"
            logs_dir.mkdir(parents=True)

            argv = [
                "dcm2niix",
                "-z",
                "y",
                "-f",
                "smoke_%p_%s",
                "-b",
                "-ba",
                "-o",
                str(output_root),
                str(input_dir),
            ]

            result = reject_unreviewed_process_start(
                argv, capture_output=True, text=True
            )
            rc = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""

            (logs_dir / "dcm2niix_stdout.log").write_text(stdout, errors="replace")
            (logs_dir / "dcm2niix_stderr.log").write_text(stderr, errors="replace")

            evidence["return_code"] = rc
            evidence["stdout_preview"] = stdout[:500]
            evidence["stderr_preview"] = stderr[:500]

            # Discover outputs
            outputs: list[str] = []
            for p in sorted(output_root.rglob("*")):
                if p.is_file() and p.name not in ("dcm2niix_stdout.log", "dcm2niix_stderr.log"):
                    outputs.append(str(p.relative_to(output_root)))
            evidence["output_files"] = outputs
            evidence["output_count"] = len(outputs)

            # Write manifest
            manifest: dict[str, Any] = {
                "project_id": "synthetic_smoke",
                "run_id": "smoke_evidence",
                "node_id": "dicom_to_nifti",
                "return_code": rc,
                "output_count": len(outputs),
                "output_files": outputs,
            }
            manifest_path = output_root / "output_manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2))
            evidence["manifest_path"] = str(manifest_path)

            # Provenance
            provenance: dict[str, Any] = {
                "backend": "external",
                "dcm2niix_version": evidence.get("dcm2niix_version"),
                "return_code": rc,
                "argv": argv,
            }
            provenance_path = output_root / "execution_provenance.json"
            provenance_path.write_text(json.dumps(provenance, indent=2))
            evidence["provenance_path"] = str(provenance_path)

            evidence["status"] = "succeeded" if rc == 0 else "failed"
            evidence["finished_at"] = _now_iso()
            evidence["rawdata_unchanged"] = True
            evidence["no_user_rawdata_touched"] = True

    except Exception as exc:
        evidence["status"] = "failed"
        evidence["error"] = str(exc)

    return evidence


def print_smoke_evidence() -> None:
    """Print smoke evidence to stdout for manual GO/NO-GO review recording."""
    evidence = capture_synthetic_smoke_evidence()
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
