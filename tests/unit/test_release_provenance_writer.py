from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
WRITER = ROOT / "desktop" / "packaging" / "write_release_provenance.py"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WRITER), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _candidate_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "candidate"
    (repo / "src" / "frontend" / "dist").mkdir(parents=True)
    (repo / "desktop" / "packaging" / "dist" / "backend_payload").mkdir(parents=True)
    (repo / "desktop" / "electron").mkdir(parents=True)
    (repo / "src" / "backend" / "app").mkdir(parents=True)
    (repo / "src" / "frontend" / "dist" / "index.html").write_text("renderer", encoding="utf-8")
    (repo / "desktop" / "packaging" / "dist" / "backend_payload" / "medimage-backend.bin").write_bytes(
        b"backend"
    )
    (repo / "desktop" / "electron" / "package.json").write_text(
        json.dumps({"version": "1.2.3", "devDependencies": {"electron": "31.7.7"}}),
        encoding="utf-8",
    )
    (repo / "src" / "backend" / "app" / "version.py").write_text(
        'APP_VERSION = "1.2.3"\n', encoding="utf-8"
    )
    (repo / "package-lock.json").write_text("{}", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "candidate")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_writer_records_clean_exact_sha_and_packaged_input_hashes(tmp_path: Path) -> None:
    repo, sha = _candidate_repo(tmp_path)
    output = repo / "release" / "build-provenance.json"

    completed = _run(
        "--repo-root",
        str(repo),
        "--output",
        str(output),
        "--expected-sha",
        sha,
        "--require-clean",
        cwd=repo,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["_schema_version"] == 1
    assert payload["git"]["sha"] == sha
    assert payload["git"]["clean"] is True
    assert payload["application_version"] == "1.2.3"
    files = {item["path"]: item for item in payload["packaged_inputs"]}
    assert files["src/frontend/dist/index.html"]["sha256"] == hashlib.sha256(b"renderer").hexdigest()
    assert files["desktop/packaging/dist/backend_payload/medimage-backend.bin"]["sha256"] == hashlib.sha256(
        b"backend"
    ).hexdigest()


def test_writer_rejects_dirty_or_wrong_release_candidate(tmp_path: Path) -> None:
    repo, sha = _candidate_repo(tmp_path)
    output = repo / "release" / "build-provenance.json"
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")

    dirty = _run(
        "--repo-root",
        str(repo),
        "--output",
        str(output),
        "--expected-sha",
        sha,
        "--require-clean",
        cwd=repo,
    )
    mismatch = _run(
        "--repo-root",
        str(repo),
        "--output",
        str(output),
        "--expected-sha",
        "0" * 40,
        cwd=repo,
    )

    assert dirty.returncode != 0
    assert "RELEASE_WORKTREE_DIRTY" in dirty.stderr
    assert mismatch.returncode != 0
    assert "RELEASE_SHA_MISMATCH" in mismatch.stderr


def test_writer_creates_release_artifact_manifest(tmp_path: Path) -> None:
    repo, sha = _candidate_repo(tmp_path)
    provenance = repo / "release" / "build-provenance.json"
    initial = _run(
        "--repo-root",
        str(repo),
        "--output",
        str(provenance),
        "--expected-sha",
        sha,
        cwd=repo,
    )
    assert initial.returncode == 0, initial.stderr
    artifact_root = repo / "desktop" / "electron" / "dist"
    (artifact_root / "win-unpacked" / "resources" / "release").mkdir(parents=True)
    (artifact_root / "win-unpacked" / "MedImage Agent.exe").write_bytes(b"electron")
    (artifact_root / "win-unpacked" / "resources" / "release" / "build-provenance.json").write_bytes(
        provenance.read_bytes()
    )
    artifact_output = repo / "release" / "release-artifacts.json"

    completed = _run(
        "--repo-root",
        str(repo),
        "--output",
        str(provenance),
        "--artifact-root",
        str(artifact_root),
        "--artifact-output",
        str(artifact_output),
        cwd=repo,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(artifact_output.read_text(encoding="utf-8"))
    assert payload["_schema_version"] == 1
    assert payload["git_sha"] == sha
    artifacts = {item["path"]: item for item in payload["artifacts"]}
    assert "win-unpacked/MedImage Agent.exe" in artifacts
    assert artifacts["win-unpacked/MedImage Agent.exe"]["sha256"] == hashlib.sha256(b"electron").hexdigest()
