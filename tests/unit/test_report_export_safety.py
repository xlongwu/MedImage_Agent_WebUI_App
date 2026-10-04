import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.tools import report_exporter as exporter


def _args(project):
    source = project / "reports/rsfmri/group_summary/dataset_summary.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"ok": True, "subjects_total": 1}), encoding="utf-8")
    return dict(project_dir=str(project), derivatives_dir=str(project / "derivatives"), reports_dir=str(project / "reports"), work_dir=str(project / "work"), exports_dir=str(project / "exports"), export_id="safe")


@pytest.mark.parametrize("case", ["outside", "rawdata", "root", "traversal"])
def test_export_rejects_unapproved_outputs_without_writes(tmp_path, case):
    args = _args(tmp_path / "project")
    args["exports_dir"] = str({"outside": tmp_path / "outside", "rawdata": tmp_path / "project/rawdata", "root": tmp_path / "project", "traversal": tmp_path / "project/exports"}[case])
    if case == "traversal": args["export_id"] = "../escape"
    with pytest.raises(SafetyError):
        exporter.export_rsfmri_report_package(**args)
    assert not list(tmp_path.rglob("*.zip"))


@pytest.mark.parametrize("operation", ["copy", "zip", "permission"])
def test_partial_export_is_removed_and_source_stays_unchanged(tmp_path, monkeypatch, operation):
    args = _args(tmp_path / "中文 项目")
    source = Path(args["reports_dir"]) / "rsfmri/group_summary/dataset_summary.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if operation == "copy":
        real_copy = exporter.shutil.copy2
        def fail_copy(src, dst, **kwargs):
            real_copy(src, dst, **kwargs)
            raise OSError("injected partial copy")
        monkeypatch.setattr(exporter.shutil, "copy2", fail_copy)
    elif operation == "zip":
        def fail_zip(package, destination):
            destination.write(b"partial zip")
            raise OSError("injected zip failure")
        monkeypatch.setattr(exporter, "_zip_directory", fail_zip)
    else:
        real_mkdir = Path.mkdir
        def mkdir(path, *positional, **kwargs):
            if path.name == "safe": raise PermissionError("injected unwritable target")
            return real_mkdir(path, *positional, **kwargs)
        monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(SafetyError, match="REPORT_EXPORT_WRITE_FAILED"):
        exporter.export_rsfmri_report_package(**args)
    assert not (Path(args["exports_dir"]) / "rsfmri_report_package/safe").exists()
    assert not list(tmp_path.rglob("*.zip"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_completed_package_reloads_and_existing_export_is_not_overwritten(tmp_path):
    args = _args(tmp_path / "中文 项目")
    result = exporter.export_rsfmri_report_package(**args)
    package = Path(result["package_dir"])
    manifest = json.loads((package / "MANIFEST.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        assert hashlib.sha256((package / item["relative_path"]).read_bytes()).hexdigest() == item["sha256"]
    with zipfile.ZipFile(result["zip_path"]) as archive:
        assert archive.testzip() is None
        assert archive.read("MANIFEST.json") == (package / "MANIFEST.json").read_bytes()
    digest = hashlib.sha256(Path(result["zip_path"]).read_bytes()).hexdigest()
    with pytest.raises(SafetyError, match="REPORT_EXPORT_ALREADY_EXISTS"):
        exporter.export_rsfmri_report_package(**args)
    assert hashlib.sha256(Path(result["zip_path"]).read_bytes()).hexdigest() == digest


def test_export_preserves_a_concurrent_archive_it_does_not_own(tmp_path, monkeypatch):
    args = _args(tmp_path / "project")
    real_build = exporter._build_report_package
    archive = Path(args["exports_dir"]) / "rsfmri_report_package/safe.zip"
    def build(**kwargs):
        result = real_build(**kwargs)
        archive.write_bytes(b"external archive")
        return result
    monkeypatch.setattr(exporter, "_build_report_package", build)
    with pytest.raises(SafetyError, match="REPORT_EXPORT_WRITE_FAILED"):
        exporter.export_rsfmri_report_package(**args)
    assert archive.read_bytes() == b"external archive"
    assert not archive.with_suffix("").exists()


@pytest.mark.parametrize("source_kind", ["rawdata", "outside"])
def test_native_report_references_cannot_export_protected_or_external_sources(tmp_path, source_kind):
    args = _args(tmp_path / "project")
    # Select the real native bridge, which consumes artifact paths from its saved validation.
    (Path(args["reports_dir"]) / "rsfmri/group_summary/dataset_summary.json").unlink()
    source = (tmp_path / "project/rawdata" if source_kind == "rawdata" else tmp_path / "outside") / "confounds.tsv"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"protected input")
    run = tmp_path / "project/preprocessing_native_runs/run"
    group = run / "artifacts/group_summary/native_group_summary.json"
    group.parent.mkdir(parents=True)
    group.write_text(json.dumps({"subject_summaries": [{"subject_id": "sub-001"}]}), encoding="utf-8")
    validation = run / "artifacts/validation_report/native_preproc_validation_report.json"
    validation.parent.mkdir(parents=True)
    validation.write_text(json.dumps({"artifact_validation": {"artifacts": [{"artifact_type": "confounds", "path": str(source)}]}}), encoding="utf-8")
    expected = "REPORT_EXPORT_RAWDATA_FORBIDDEN" if source_kind == "rawdata" else "REPORT_EXPORT_SOURCE_OUTSIDE_PROJECT"
    with pytest.raises(SafetyError, match=expected):
        exporter.export_rsfmri_report_package(**args)
    assert source.read_bytes() == b"protected input"
    assert not list(Path(args["exports_dir"]).rglob("MANIFEST.json"))
    assert not list(Path(args["exports_dir"]).rglob("*.zip"))
