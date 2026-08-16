"""Verify sandbox outputs before their first eligible artifact registration."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.sandbox import SandboxAttemptRecord


_ALLOWED_SUFFIXES = {".nii", ".gz", ".json", ".csv", ".tsv", ".txt", ".html", ".log", ".npy"}


class SandboxOutputVerifier:
    def verify_and_promote(
        self,
        *,
        attempt: SandboxAttemptRecord,
        sandbox_root: str | Path,
        approved_output_roots: tuple[str, ...],
    ) -> dict[str, object]:
        root = Path(sandbox_root).expanduser().resolve()
        source_root = root / "output"
        if not source_root.is_dir():
            raise SafetyError("SANDBOX_OUTPUT_INVALID", code="SANDBOX_OUTPUT_INVALID")
        files = self._verified_files(source_root)
        if not files:
            raise SafetyError("SANDBOX_OUTPUT_INVALID", code="SANDBOX_OUTPUT_INVALID")
        target_root = self._target_root(attempt, approved_output_roots)
        target_root.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, object]] = []
        try:
            for source in files:
                relative = source.relative_to(source_root)
                destination = target_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    raise SafetyError("SANDBOX_OUTPUT_ALREADY_EXISTS", code="SANDBOX_OUTPUT_ALREADY_EXISTS")
                temporary = destination.with_name(f".{destination.name}.sandbox-tmp")
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
                self._reload(destination)
                entries.append({
                    "relative_path": relative.as_posix(),
                    "sha256": self._sha256(destination),
                    "size_bytes": destination.stat().st_size,
                })
        except Exception:
            # The target is a new, attempt-specific root. It is safe to remove
            # only that exact root after a failed promotion.
            shutil.rmtree(target_root, ignore_errors=True)
            raise
        manifest = {
            "schema_version": 1,
            "sandbox_id": attempt.sandbox_id,
            "files": entries,
            "output_root": str(target_root),
        }
        manifest["output_manifest_hash"] = stable_hash(manifest)
        return manifest

    @staticmethod
    def _target_root(attempt: SandboxAttemptRecord, roots: tuple[str, ...]) -> Path:
        if not roots:
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        root = Path(roots[0]).expanduser().resolve()
        if root.name.casefold() == "rawdata":
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT")
        target = (root / attempt.run_id / attempt.node_id / attempt.attempt_id).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise SafetyError("SANDBOX_PATH_OUTSIDE_PROJECT", code="SANDBOX_PATH_OUTSIDE_PROJECT") from exc
        if target.exists():
            raise SafetyError("SANDBOX_OUTPUT_ALREADY_EXISTS", code="SANDBOX_OUTPUT_ALREADY_EXISTS")
        return target

    def _verified_files(self, root: Path) -> list[Path]:
        results: list[Path] = []
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                if path.is_symlink():
                    raise SafetyError("SANDBOX_OUTPUT_UNSAFE", code="SANDBOX_OUTPUT_UNSAFE")
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError as exc:
                raise SafetyError("SANDBOX_OUTPUT_UNSAFE", code="SANDBOX_OUTPUT_UNSAFE") from exc
            if path.suffix.lower() not in _ALLOWED_SUFFIXES:
                raise SafetyError("SANDBOX_OUTPUT_INVALID", code="SANDBOX_OUTPUT_INVALID")
            if path.stat().st_size > 8 * 1024**3:
                raise SafetyError("SANDBOX_OUTPUT_INVALID", code="SANDBOX_OUTPUT_INVALID")
            results.append(path)
        return sorted(results)

    @staticmethod
    def _reload(path: Path) -> None:
        if path.suffix.lower() == ".json":
            with path.open(encoding="utf-8") as handle:
                json.load(handle)
        elif path.suffix.lower() in {".csv", ".tsv", ".txt", ".html", ".log"}:
            path.read_text(encoding="utf-8", errors="strict")
        elif path.stat().st_size == 0:
            raise SafetyError("SANDBOX_OUTPUT_INVALID", code="SANDBOX_OUTPUT_INVALID")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
