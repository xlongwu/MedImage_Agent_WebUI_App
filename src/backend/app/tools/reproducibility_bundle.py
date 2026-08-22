from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.backend.app.tools.artifact_utils import (
    is_safe_artifact_id,
    sha256_file,
    write_json_artifact,
)
from src.backend.app.runtime.sandbox_process_runner import reject_unreviewed_process_start

EXCLUDED_PARTS = {
    "third_party",
    ".git",
    "node_modules",
    "__pycache__",
    "rawdata",
    "derivatives",
}

EXCLUDED_SUFFIXES = {
    ".nii",
    ".gz",
    ".mat",
    ".zip",
}

ALLOWED_ROOTS = [
    "specs",
    "examples",
    "outputs/work/experiments",
    "outputs/work/artifacts",
    "outputs/work/dpabi",
    "reports",
    "logs",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _bundle_id_now() -> str:
    return "bundle_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_cmd(cmd: list[str], timeout: int = 10) -> dict[str, Any]:
    try:
        completed = reject_unreviewed_process_start(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": completed.stdout[:20_000],
            "stderr": completed.stderr[:20_000],
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
        }


def _environment_snapshot() -> dict[str, Any]:
    pip_freeze = _run_cmd([sys.executable, "-m", "pip", "freeze"])
    node_version = _run_cmd(["node", "--version"])
    npm_version = _run_cmd(["npm", "--version"])
    git_commit = _run_cmd(["git", "rev-parse", "HEAD"])
    git_status = _run_cmd(["git", "status", "--short"])

    pip_lines = pip_freeze.get("stdout", "").splitlines()[:200]

    return {
        "generated_at": _now_iso(),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "platform": {
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "cwd": str(Path.cwd()),
        "pip_freeze_head": pip_lines,
        "node_version": node_version,
        "npm_version": npm_version,
        "git_commit": git_commit,
        "git_status_short": git_status,
        "note": "MATLAB, SPM, and DPABI are not executed during bundle creation.",
    }


def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)

    if any(part in EXCLUDED_PARTS for part in parts):
        return True

    name = path.name.lower()
    if name.endswith(".nii") or name.endswith(".nii.gz"):
        return True

    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return True

    return False


def _candidate_files() -> list[Path]:
    candidates: list[Path] = []

    for root_text in ALLOWED_ROOTS:
        root = Path(root_text)
        if not root.exists():
            continue

        if root.is_file():
            candidates.append(root)
            continue

        for path in root.rglob("*"):
            if path.is_file():
                candidates.append(path)

    for root_file in [Path("README.md"), Path("pyproject.toml"), Path("package.json")]:
        if root_file.exists():
            candidates.append(root_file)

    return sorted(set(candidates))


def _copy_artifacts(
    bundle_dir: Path,
    max_file_size_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    copied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    files_dir = bundle_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    for src in _candidate_files():
        if _is_excluded(src):
            skipped.append({
                "path": str(src),
                "reason": "excluded_path_or_suffix",
            })
            continue

        try:
            size = src.stat().st_size
        except Exception as exc:
            skipped.append({
                "path": str(src),
                "reason": f"stat_failed: {exc}",
            })
            continue

        if size > max_file_size_bytes:
            skipped.append({
                "path": str(src),
                "size_bytes": size,
                "reason": "file_too_large",
            })
            continue

        dst = files_dir / src
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

        copied.append({
            "source_path": str(src),
            "bundle_path": str(dst.relative_to(bundle_dir)),
            "size_bytes": size,
            "sha256": sha256_file(src),
        })

    return copied, skipped


def _write_bundle_index(work_dir: str, item: dict[str, Any]) -> None:
    root = Path(work_dir) / "bundles"
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "bundle_index.json"

    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = {"bundles": []}
    else:
        index = {"bundles": []}

    bundles = [
        existing for existing in index.get("bundles", [])
        if existing.get("bundle_id") != item.get("bundle_id")
    ]
    bundles.append(item)
    bundles = sorted(bundles, key=lambda x: x.get("created_at", ""), reverse=True)

    index = {
        "ok": True,
        "updated_at": _now_iso(),
        "bundles_total": len(bundles),
        "bundles": bundles,
    }

    write_json_artifact(index_path, index)


def create_reproducibility_bundle(
    bundle_id: str | None = None,
    work_dir: str = "./work",
    report_dir: str = "./reports",
    include_logs: bool = True,
    include_reports: bool = True,
    include_artifact_index: bool = True,
    max_file_size_bytes: int = 2_000_000,
) -> dict[str, Any]:
    bundle_id = bundle_id or _bundle_id_now()

    if not is_safe_artifact_id(bundle_id):
        return {
            "ok": False,
            "errors": ["Invalid bundle_id."],
            "warnings": [],
        }

    bundle_root = Path(work_dir) / "bundles"
    bundle_dir = bundle_root / bundle_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    report_out = Path(report_dir) / "bundles"
    report_out.mkdir(parents=True, exist_ok=True)

    environment = _environment_snapshot()
    copied, skipped = _copy_artifacts(
        bundle_dir=bundle_dir,
        max_file_size_bytes=max_file_size_bytes,
    )

    artifact_manifest = {
        "bundle_id": bundle_id,
        "copied_total": len(copied),
        "skipped_total": len(skipped),
        "copied": copied,
        "skipped": skipped,
    }

    manifest = {
        "ok": True,
        "bundle_id": bundle_id,
        "created_at": _now_iso(),
        "bundle_dir": str(bundle_dir),
        "include_logs": include_logs,
        "include_reports": include_reports,
        "include_artifact_index": include_artifact_index,
        "max_file_size_bytes": max_file_size_bytes,
        "files_copied": len(copied),
        "files_skipped": len(skipped),
        "safety": {
            "pipelines_executed": False,
            "matlab_launched": False,
            "dpabi_executed": False,
            "dparsf_run_executed": False,
            "dpabi_gui_called": False,
            "rawdata_packaged": False,
            "third_party_packaged": False,
            "rawdata_modified": False,
            "files_deleted": False,
        },
        "outputs": [],
        "warnings": [],
        "errors": [],
    }

    environment_path = bundle_dir / "environment_snapshot.json"
    artifact_manifest_path = bundle_dir / "artifact_manifest.json"
    manifest_path = bundle_dir / "manifest.json"
    readme_path = bundle_dir / "README.md"
    zip_path = bundle_dir / "bundle.zip"
    report_path = report_out / f"{bundle_id}_bundle_report.md"

    write_json_artifact(environment_path, environment)
    write_json_artifact(artifact_manifest_path, artifact_manifest)

    readme_lines = []
    readme_lines.append(f"# Reproducibility Bundle: {bundle_id}")
    readme_lines.append("")
    readme_lines.append("This bundle captures selected project artifacts for review and reproducibility.")
    readme_lines.append("")
    readme_lines.append("## Contents")
    readme_lines.append("")
    readme_lines.append("- manifest.json")
    readme_lines.append("- environment_snapshot.json")
    readme_lines.append("- artifact_manifest.json")
    readme_lines.append("- files/")
    readme_lines.append("")
    readme_lines.append("## Safety")
    readme_lines.append("")
    readme_lines.append("- Pipelines executed during packaging: false")
    readme_lines.append("- MATLAB launched during packaging: false")
    readme_lines.append("- DPABI executed during packaging: false")
    readme_lines.append("- Rawdata packaged: false")
    readme_lines.append("- Third-party toolboxes packaged: false")
    readme_lines.append("")
    readme_lines.append("## Notes")
    readme_lines.append("")
    readme_lines.append("Large binary files, rawdata, third_party toolboxes, node_modules, .git, and derivatives are excluded by default.")

    readme_path.write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    manifest["outputs"] = [
        str(manifest_path),
        str(environment_path),
        str(artifact_manifest_path),
        str(readme_path),
        str(zip_path),
        str(report_path),
    ]

    write_json_artifact(manifest_path, manifest)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_dir.rglob("*"):
            if path == zip_path:
                continue
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(bundle_dir)))

    zip_sha = sha256_file(zip_path)
    manifest["zip_sha256"] = zip_sha
    manifest["zip_size_bytes"] = zip_path.stat().st_size

    write_json_artifact(manifest_path, manifest)

    report_lines = []
    report_lines.append(f"# Reproducibility Bundle Report: {bundle_id}")
    report_lines.append("")
    report_lines.append(f"- Bundle directory: `{bundle_dir}`")
    report_lines.append(f"- Zip: `{zip_path}`")
    report_lines.append(f"- Zip SHA256: `{zip_sha}`")
    report_lines.append(f"- Files copied: {len(copied)}")
    report_lines.append(f"- Files skipped: {len(skipped)}")
    report_lines.append("")
    report_lines.append("## Safety")
    report_lines.append("")
    for key, value in manifest["safety"].items():
        report_lines.append(f"- {key}: {value}")
    report_lines.append("")
    report_lines.append("## Skipped Files")
    report_lines.append("")
    for item in skipped[:100]:
        report_lines.append(f"- `{item.get('path')}`: {item.get('reason')}")
    if len(skipped) > 100:
        report_lines.append(f"- ... {len(skipped) - 100} more")

    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    index_item = {
        "bundle_id": bundle_id,
        "created_at": manifest["created_at"],
        "bundle_dir": str(bundle_dir),
        "zip_path": str(zip_path),
        "zip_sha256": zip_sha,
        "zip_size_bytes": manifest["zip_size_bytes"],
        "files_copied": len(copied),
        "files_skipped": len(skipped),
        "manifest_path": str(manifest_path),
    }

    _write_bundle_index(work_dir, index_item)

    return manifest


def list_reproducibility_bundles(
    work_dir: str = "./work",
) -> dict[str, Any]:
    index_path = Path(work_dir) / "bundles" / "bundle_index.json"

    if not index_path.exists():
        return {
            "ok": True,
            "bundles_total": 0,
            "bundles": [],
            "warnings": ["No bundle index found."],
        }

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "bundles_total": index.get("bundles_total", 0),
            "bundles": index.get("bundles", []),
            "warnings": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"Failed to read bundle index: {exc}"],
            "warnings": [],
        }


def inspect_reproducibility_bundle(
    bundle_id: str,
    work_dir: str = "./work",
) -> dict[str, Any]:
    if not is_safe_artifact_id(bundle_id):
        return {
            "ok": False,
            "errors": ["Invalid bundle_id."],
            "warnings": [],
        }

    bundle_dir = Path(work_dir) / "bundles" / bundle_id
    manifest_path = bundle_dir / "manifest.json"

    if not manifest_path.exists():
        return {
            "ok": False,
            "errors": [f"Bundle not found: {bundle_id}"],
            "warnings": [],
        }

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return {
            "ok": True,
            "bundle_id": bundle_id,
            "manifest": manifest,
            "bundle_dir": str(bundle_dir),
            "warnings": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "errors": [f"Failed to read bundle manifest: {exc}"],
            "warnings": [],
        }
