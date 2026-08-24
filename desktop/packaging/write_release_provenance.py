from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


SCHEMA_VERSION = 1
ARTIFACT_NAMES = {
    "MedImage Agent.exe",
    "MedImage Agent Setup.exe",
    "app.asar",
    "build-provenance.json",
    "latest.yml",
    "medimage-backend.bin",
}


class ProvenanceError(RuntimeError):
    pass


def _run(command: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ProvenanceError(f"COMMAND_FAILED: {' '.join(command)} :: {detail}")
    return completed.stdout.strip()


def _git(repo_root: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=repo_root)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_file(repo_root: Path, path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ProvenanceError(f"PROVENANCE_INPUT_OUTSIDE_REPOSITORY: {resolved}") from exc
    return {
        "path": relative,
        "size": resolved.stat().st_size,
        "sha256": _sha256(resolved),
    }


def _version(repo_root: Path) -> str:
    version_path = repo_root / "src" / "backend" / "app" / "version.py"
    if version_path.is_file():
        match = re.search(
            r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']',
            version_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            return match.group(1)
    package_path = repo_root / "desktop" / "electron" / "package.json"
    if package_path.is_file():
        return str(json.loads(package_path.read_text(encoding="utf-8")).get("version") or "unknown")
    return "unknown"


def _tool_version(command: list[str], repo_root: Path) -> str | None:
    try:
        executable = shutil.which(command[0])
        if executable is None:
            return None
        return _run([executable, *command[1:]], cwd=repo_root)
    except (OSError, ProvenanceError):
        return None


def _packaged_inputs(repo_root: Path) -> list[dict[str, Any]]:
    candidates: set[Path] = set()
    for relative in (
        "pyproject.toml",
        "package-lock.json",
        "src/frontend/package-lock.json",
        "desktop/electron/package-lock.json",
        "desktop/electron/package.json",
        "desktop/electron/main.cjs",
        "desktop/electron/preload.cjs",
        "desktop/electron/electron-builder.yml",
        "desktop/electron/build-dist.cjs",
    ):
        path = repo_root / relative
        if path.is_file():
            candidates.add(path)
    for relative_root in (
        "src/frontend/dist",
        "desktop/packaging/dist/backend_payload",
    ):
        root = repo_root / relative_root
        if root.is_dir():
            candidates.update(path for path in root.rglob("*") if path.is_file())
    return [_relative_file(repo_root, path) for path in sorted(candidates)]


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os_getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def os_getpid() -> int:
    import os

    return os.getpid()


def write_build_provenance(
    *,
    repo_root: Path,
    output: Path,
    expected_sha: str | None,
    require_clean: bool,
) -> dict[str, Any]:
    actual_sha = _git(repo_root, "rev-parse", "HEAD").lower()
    if expected_sha and actual_sha != expected_sha.strip().lower():
        raise ProvenanceError(
            f"RELEASE_SHA_MISMATCH: expected {expected_sha.strip().lower()}, actual {actual_sha}"
        )
    status = _git(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    clean = status == ""
    if require_clean and not clean:
        changed = [line for line in status.splitlines() if line.strip()]
        raise ProvenanceError(
            "RELEASE_WORKTREE_DIRTY: " + ", ".join(changed[:20])
        )
    electron_package_path = repo_root / "desktop" / "electron" / "package.json"
    electron_package = (
        json.loads(electron_package_path.read_text(encoding="utf-8"))
        if electron_package_path.is_file()
        else {}
    )
    payload: dict[str, Any] = {
        "_schema_version": SCHEMA_VERSION,
        "application_version": _version(repo_root),
        "built_at_utc": datetime.now(UTC).isoformat(),
        "git": {
            "sha": actual_sha,
            "clean": clean,
        },
        "toolchain": {
            "python": sys.version.split()[0],
            "node": _tool_version(["node", "--version"], repo_root),
            "npm": _tool_version(["npm", "--version"], repo_root),
            "electron": electron_package.get("devDependencies", {}).get("electron"),
            "electron_builder": electron_package.get("devDependencies", {}).get(
                "electron-builder"
            ),
        },
        "packaged_inputs": _packaged_inputs(repo_root),
    }
    _atomic_write_json(output, payload)
    return payload


def write_artifact_manifest(
    *, provenance_path: Path, artifact_root: Path, output: Path
) -> dict[str, Any]:
    if not provenance_path.is_file():
        raise ProvenanceError(f"BUILD_PROVENANCE_MISSING: {provenance_path}")
    if not artifact_root.is_dir():
        raise ProvenanceError(f"RELEASE_ARTIFACT_ROOT_MISSING: {artifact_root}")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    artifacts: list[dict[str, Any]] = []
    for path in sorted(item for item in artifact_root.rglob("*") if item.is_file()):
        if path.name not in ARTIFACT_NAMES and path.suffix.lower() not in {".blockmap"}:
            continue
        artifacts.append(
            {
                "path": path.relative_to(artifact_root).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    if not artifacts:
        raise ProvenanceError("RELEASE_ARTIFACTS_EMPTY")
    payload = {
        "_schema_version": SCHEMA_VERSION,
        "git_sha": provenance.get("git", {}).get("sha"),
        "application_version": provenance.get("application_version"),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "artifacts": artifacts,
    }
    _atomic_write_json(output, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write exact-SHA desktop release provenance.")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--artifact-root")
    parser.add_argument("--artifact-output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    output = Path(args.output).resolve()
    try:
        if args.artifact_root or args.artifact_output:
            if not args.artifact_root or not args.artifact_output:
                raise ProvenanceError(
                    "ARTIFACT_MANIFEST_ARGUMENTS_INCOMPLETE: --artifact-root and --artifact-output are required together"
                )
            write_artifact_manifest(
                provenance_path=output,
                artifact_root=Path(args.artifact_root).resolve(),
                output=Path(args.artifact_output).resolve(),
            )
        else:
            write_build_provenance(
                repo_root=repo_root,
                output=output,
                expected_sha=args.expected_sha,
                require_clean=bool(args.require_clean),
            )
    except (OSError, ValueError, json.JSONDecodeError, ProvenanceError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
