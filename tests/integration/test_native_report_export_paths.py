"""Real shallow/deep TEMP comparison; no Windows policy changes."""
import hashlib
import json
import shutil
import tempfile
import traceback
from pathlib import Path

import pytest

from src.backend.app.tools.native_preproc_run_validator import validate_native_preproc_run
from tests.integration.native_preproc_fixtures import run_synthetic_native_full


@pytest.mark.parametrize("depth", [0, 2])
def test_probe_export_is_independent_of_inherited_temp_depth(tmp_path, monkeypatch, depth):
    project = tmp_path / "project"
    project.mkdir()
    _, run = run_synthetic_native_full(project, run_id="report-probe")
    inherited = tmp_path / "temp"
    for _ in range(depth):
        inherited /= "long-temp-segment-" + "x" * 43
    inherited.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(inherited))
    failures = []
    real_copy = shutil.copy2
    real_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        try:
            return real_mkdir(path, *args, **kwargs)
        except OSError as exc:
            failures.append({"operation": "Path.mkdir", "errno": exc.errno, "winerror": getattr(exc, "winerror", None),
                             "destination_length": len(str(path.resolve())), "destination_hash": hashlib.sha256(str(path.resolve()).encode()).hexdigest(),
                             "stack_functions": [frame.name for frame in traceback.extract_tb(exc.__traceback__)]})
            raise

    def copy(source, destination, **kwargs):
        try:
            return real_copy(source, destination, **kwargs)
        except OSError as exc:
            failures.append({
                "operation": "shutil.copy2", "errno": exc.errno, "winerror": getattr(exc, "winerror", None),
                "source_length": len(str(Path(source).resolve())), "destination_length": len(str(Path(destination).resolve())),
                "source_name": Path(source).name, "destination_name": Path(destination).name,
                "destination_hash": hashlib.sha256(str(Path(destination).resolve()).encode()).hexdigest(),
                "stack_functions": [frame.name for frame in traceback.extract_tb(exc.__traceback__)],
            })
            raise

    monkeypatch.setattr(shutil, "copy2", copy)
    monkeypatch.setattr(Path, "mkdir", mkdir)
    result = validate_native_preproc_run(run.run_dir, project_dir=project, require_report_chain=True, probe_exporter=True)
    check = next(item for item in result["checks"] if item["name"] == "report_exporter_probe_uses_native_outputs")
    print(json.dumps({"temp_depth": depth, "temp_length": len(str(inherited.resolve())), "probe_status": check["status"], "failed_operations": failures}))
    assert result["ok"] is True, {"probe_status": check["status"], "failed_operations": failures}
    assert not list(inherited.iterdir())
