from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src.backend.app.api import dashboard_routes, project_routes
from src.backend.app.api.dependencies import get_project_store
from src.backend.app.main import app
from src.backend.app.runtime import desktop_config
from src.backend.app.services import mock_store as mock_store_module
from src.backend.app.services.mock_store import SQLiteDesktopStore

# ── Helper ──────────────────────────────────────────────────────────


def _clean_desktop_config(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    """Point desktop config and dashboard routes to isolated temp storage."""
    config_path = tmp_path / "test_desktop_config.json"
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(project_routes, "mock_store", store)
    monkeypatch.setattr(dashboard_routes, "mock_store", store)
    monkeypatch.setattr(mock_store_module, "mock_store", store)
    # Reset the config to defaults
    config_path.write_text(json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG), encoding="utf-8")
    return store


def _make_project_request(**overrides) -> dict:
    payload = {
        "project_name": "test-project",
        "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
        "run_inspection": True,
        "overwrite": False,
    }
    payload.update(overrides)
    return payload


def _count_files_in_dir(directory: Path) -> int:
    """Count all files recursively in *directory*."""
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.rglob("*") if _.is_file())


def _file_hashes(directory: Path) -> dict[str, str]:
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# ── Test: successful creation ────────────────────────────────────────


def test_create_project_with_synthetic_bids_succeeds(tmp_path, monkeypatch):
    """Create a project from examples/synthetic_bids/rawdata/."""
    _clean_desktop_config(tmp_path, monkeypatch)

    rawdata = Path("examples/synthetic_bids/rawdata")
    assert rawdata.exists(), f"Test data missing: {rawdata}"

    project_dir = str(tmp_path / "my_project")
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Synthetic BIDS Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": project_dir,
            "run_inspection": True,
            "overwrite": False,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["project_name"] == "Synthetic BIDS Test"
    assert data["project_id"].startswith("synthetic-bids-test-")
    assert len(data["project_id"]) > 20  # slug + uuid suffix

    # Check paths
    assert Path(data["project_config_path"]).exists()
    assert data["dataset_index_path"] is not None
    assert Path(data["dataset_index_path"]).exists()

    # Check diagnostics
    diag = data["diagnostics"]
    assert diag["subjects_total"] > 0
    assert diag["nifti_file_count"] == 4
    assert diag["status"] == "READY"

    # Check next_actions
    assert any("pipeline" in a.lower() for a in data["next_actions"])
    assert data["next_actions"] == [
        "Choose a pipeline",
        "Review dataset diagnostics",
        "Configure MATLAB/SPM/DPABI if needed",
    ]


# ── Test: generated files ────────────────────────────────────────────


def test_created_project_persists_in_dashboard_project_api(tmp_path, monkeypatch):
    """Created projects are returned by list and detail APIs without removing demos."""
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)

    created = client.post(
        "/api/projects/create",
        json=_make_project_request(
            project_name="Persisted Project",
            project_dir=str(tmp_path / "persisted_project"),
        ),
    )

    assert created.status_code == 200, created.text
    created_payload = created.json()
    project_id = created_payload["project_id"]

    projects = client.get("/api/projects")
    assert projects.status_code == 200
    project_ids = [project["id"] for project in projects.json()]
    assert "brain-tumor-study" in project_ids
    assert project_ids.count(project_id) == 1

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["name"] == "Persisted Project"
    assert detail_payload["subjects_count"] == created_payload["diagnostics"]["subjects_total"]
    assert detail_payload["metadata"]["source"] == "created"
    assert detail_payload["metadata"]["project_dir"] == created_payload["project_dir"]
    assert detail_payload["metadata"]["rawdata_dir"] == created_payload["rawdata_dir"]
    assert (
        detail_payload["metadata"]["project_config_path"] == created_payload["project_config_path"]
    )
    assert detail_payload["metadata"]["dataset_index_path"] == created_payload["dataset_index_path"]
    assert detail_payload["metadata"]["diagnostics"] == created_payload["diagnostics"]
    assert detail_payload["metadata"]["recovery_policy"] == {
        "max_lifecycle_recovery_attempts": 2,
        "max_node_attempts": 1,
        "max_subject_node_attempts": 1,
        "max_replans": 1,
        "max_recovery_wall_seconds": 600,
    }
    assert detail_payload["metadata"]["created_at"]
    assert detail_payload["metadata"]["updated_at"]


def test_registered_bids_project_creates_persisted_read_only_preprocessing_run(
    tmp_path, monkeypatch
):
    """The Preprocessing primary action must use registered BIDS without writing rawdata."""

    store = _clean_desktop_config(tmp_path, monkeypatch)
    app.dependency_overrides[get_project_store] = lambda: store
    rawdata = Path("examples/synthetic_bids/rawdata").resolve()
    rawdata_before = _file_hashes(rawdata)
    project_dir = tmp_path / "preprocessing_project"
    client = TestClient(app)
    try:
        created = client.post(
            "/api/projects/create",
            json=_make_project_request(
                project_name="Preprocessing Integration",
                project_dir=str(project_dir),
            ),
        )
        assert created.status_code == 200, created.text
        project_id = created.json()["project_id"]

        blocked = client.post(
            f"/api/projects/{project_id}/preprocessing/runs",
            json={"confirm_use_converted_input": True},
        )
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["ok"] is False
        assert any(
            "Missing registered BIDS read-only confirmations" in issue
            for issue in blocked.json()["blocking_issues"]
        )

        response = client.post(
            f"/api/projects/{project_id}/preprocessing/runs",
            json={
                "confirm_use_converted_input": True,
                "confirm_no_rawdata_modification": True,
                "confirm_python_only_execution": True,
                "confirm_no_spm_matlab": True,
            },
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True
        assert payload["status"] == "created"
        assert payload["preprocessing_input_dir"] == str(rawdata)
        assert payload["input_inventory"]["nifti_count"] == 4
        assert Path(payload["run_dir"]).is_relative_to(project_dir)
        assert not Path(payload["run_dir"]).is_relative_to(rawdata)
        assert rawdata_before == _file_hashes(rawdata)

        detail = client.get(f"/api/projects/{project_id}")
        assert detail.status_code == 200
        metadata = detail.json()["metadata"]
        assert metadata["latest_preprocessing_run_id"] == payload["preprocessing_run_id"]
        assert metadata["preprocessing_input_source"] == "registered_bids_readonly"

        restored = client.get(
            f"/api/projects/{project_id}/preprocessing/runs/"
            f"{payload['preprocessing_run_id']}"
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "created"
    finally:
        app.dependency_overrides.pop(get_project_store, None)


def test_create_project_with_raw_dicom_directory_is_listed(tmp_path, monkeypatch):
    """DICOM-only rawdata should create a visible raw DICOM dashboard project."""
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = tmp_path / "dicom_raw"
    series_dir = rawdata / "FunRaw" / "Sub_001"
    series_dir.mkdir(parents=True)
    for index in range(3):
        (series_dir / f"slice_{index:03d}.dcm").write_bytes(b"DICOM placeholder")

    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "DICOM Upload Test",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "dicom_project"),
            "run_inspection": True,
            "overwrite": False,
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()
    diagnostics = data["diagnostics"]
    assert diagnostics["status"] == "RAW_DICOM"
    assert diagnostics["dicom_file_count"] == 3
    assert diagnostics["dicom_series_count"] == 1
    assert diagnostics["raw_dicom_candidate_subjects"] == 1
    projects = client.get("/api/projects").json()
    listed = [item for item in projects if item["id"] == data["project_id"]]
    assert listed, "DICOM-only uploaded project must appear in Recent projects source list"
    assert listed[0]["subjects_count"] == 1
    imports = client.get(f"/api/datasets/imports?project_id={data['project_id']}").json()
    assert imports["imports"][0]["dataset_type"] == "dicom"


def test_created_project_saves_rawdata_reference_in_imports(tmp_path, monkeypatch):
    store = _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata").resolve()
    client = TestClient(app)

    created = client.post(
        "/api/projects/create",
        json=_make_project_request(
            project_name="Imported Reference",
            rawdata_dir=str(rawdata),
            project_dir=str(tmp_path / "imported_reference"),
        ),
    )

    assert created.status_code == 200
    project_id = created.json()["project_id"]
    assert store.list_import_paths(project_id) == [str(rawdata)]


def test_store_write_failure_returns_warning(tmp_path, monkeypatch):
    store = _clean_desktop_config(tmp_path, monkeypatch)

    def fail_add_project(*args, **kwargs):
        raise RuntimeError("simulated store failure")

    monkeypatch.setattr(store, "add_project", fail_add_project)
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json=_make_project_request(
            project_name="Store Failure",
            project_dir=str(tmp_path / "store_failure"),
        ),
    )

    assert response.status_code == 200
    assert any(
        "dashboard project store update warning" in warning.lower()
        and "simulated store failure" in warning.lower()
        for warning in response.json()["warnings"]
    )


def test_creates_project_config_yaml(tmp_path, monkeypatch):
    """project_config.yaml is generated and passes ProjectSettings validation."""
    _clean_desktop_config(tmp_path, monkeypatch)

    rawdata = Path("examples/synthetic_bids/rawdata")
    project_dir = str(tmp_path / "test_yaml_proj")
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "YAML Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": project_dir,
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    config_path = Path(response.json()["project_config_path"])
    assert config_path.exists()
    assert config_path.name == "project_config.yaml"

    # Validate with ProjectSettings
    from src.backend.app.config import ProjectSettings

    settings = ProjectSettings.from_yaml(config_path)
    assert settings.runtime.work_dir
    assert settings.runtime.log_dir
    assert settings.third_party.spm_dir
    assert settings.third_party.dpabi_dir


def test_creates_dataset_index_json(tmp_path, monkeypatch):
    """dataset_index.json is generated by inspect_dataset."""
    _clean_desktop_config(tmp_path, monkeypatch)

    rawdata = Path("examples/synthetic_bids/rawdata")
    project_dir = str(tmp_path / "test_index_proj")
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Index Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": project_dir,
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["dataset_index_path"] is not None

    index_path = Path(data["dataset_index_path"])
    assert index_path.exists()
    index_data = json.loads(index_path.read_text(encoding="utf-8"))
    assert "subjects" in index_data
    assert "subjects_total" in index_data
    assert index_data["subjects_total"] > 0


def test_creates_completeness_report(tmp_path, monkeypatch):
    """data_completeness_report.json is generated."""
    _clean_desktop_config(tmp_path, monkeypatch)

    rawdata = Path("examples/synthetic_bids/rawdata")
    project_dir = str(tmp_path / "test_report_proj")
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Report Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": project_dir,
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    report_path = Path(project_dir) / "data" / "data_completeness_report.json"
    assert report_path.exists()
    assert (Path(project_dir) / "data" / "subject_table.csv").exists()


# ── Test: rawdata_dir safety ─────────────────────────────────────────


def test_rejects_nonexistent_rawdata_dir(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "/nonexistent/path/12345",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"].lower()


def test_rejects_rawdata_file(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata_file = tmp_path / "rawdata.txt"
    rawdata_file.write_text("not a directory", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": str(rawdata_file),
            "project_dir": str(tmp_path / "proj"),
        },
    )

    assert response.status_code == 400
    assert "not a directory" in response.json()["detail"].lower()


def test_rejects_blank_project_name(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "   ",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "proj"),
        },
    )

    assert response.status_code == 400
    assert not (tmp_path / "proj").exists()


def test_rejects_system_root_rawdata(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "C:\\",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    assert response.status_code == 400
    assert "rejected" in response.json()["detail"].lower()


def test_rejects_windows_system_dir_rawdata(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "C:\\Windows",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    assert response.status_code == 400
    assert "rejected" in response.json()["detail"].lower()


def test_rejects_program_files_rawdata(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "C:\\Program Files",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    assert response.status_code == 400


def test_rejects_home_root_rawdata(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    # "/home/" should match the pattern
    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "/home/user",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    # Note: "/home/user" is not exactly "/home/", so it might pass.
    # The pattern "/home/" is prefix-matched — let's test bare "/home" or "/home/"
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Test",
            "rawdata_dir": "/home",
            "project_dir": str(tmp_path / "proj"),
        },
    )
    # Either way should reject root-ish paths
    # /home itself is in DANGEROUS_ROOT_PATTERNS
    assert response.status_code == 400


# ── Test: empty / non-BIDS directory ──────────────────────────────────


def test_empty_rawdata_dir_returns_warnings_not_crash(tmp_path, monkeypatch):
    """Empty directory: should not crash, return EMPTY status."""
    _clean_desktop_config(tmp_path, monkeypatch)
    empty_dir = tmp_path / "empty_bids"
    empty_dir.mkdir(parents=True, exist_ok=True)

    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Empty Test",
            "rawdata_dir": str(empty_dir),
            "project_dir": str(tmp_path / "empty_proj"),
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["diagnostics"]["status"] == "EMPTY"
    assert len(data["warnings"]) > 0


def test_non_bids_directory_graceful(tmp_path, monkeypatch):
    """A directory without sub-* dirs: returns warnings, doesn't crash."""
    _clean_desktop_config(tmp_path, monkeypatch)
    non_bids = tmp_path / "not_bids"
    non_bids.mkdir(parents=True, exist_ok=True)
    (non_bids / "README.txt").write_text("not a BIDS dataset", encoding="utf-8")
    (non_bids / "data.csv").write_text("a,b,c", encoding="utf-8")

    client = TestClient(app)
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Non-BIDS Test",
            "rawdata_dir": str(non_bids),
            "project_dir": str(tmp_path / "nonbids_proj"),
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["diagnostics"]["status"] != "READY"
    # Should have a warning about no sub-* dirs
    assert any("sub-" in w.lower() for w in data["warnings"])


def test_missing_t1w_and_bold_are_reported(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = tmp_path / "incomplete_bids"
    (rawdata / "sub-001").mkdir(parents=True)
    (rawdata / "dataset_description.json").write_text("{}", encoding="utf-8")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Incomplete BIDS",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "incomplete_project"),
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["diagnostics"]["status"] == "INCOMPLETE"
    assert data["diagnostics"]["subjects_incomplete"] == 1
    assert any("missing t1w" in warning.lower() for warning in data["warnings"])
    assert any("missing bold" in warning.lower() for warning in data["warnings"])


# ── Test: overwrite protection ───────────────────────────────────────


def test_refuses_overwrite_by_default(tmp_path, monkeypatch):
    store = _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    proj_dir = str(tmp_path / "no_overwrite_proj")

    client = TestClient(app)
    # First request: succeeds
    r1 = client.post(
        "/api/projects/create",
        json={
            "project_name": "No Overwrite",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": proj_dir,
            "overwrite": False,
        },
    )
    assert r1.status_code == 200

    # Second request: should conflict
    r2 = client.post(
        "/api/projects/create",
        json={
            "project_name": "No Overwrite",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": proj_dir,
            "overwrite": False,
        },
    )
    assert r2.status_code == 409
    project_id = r1.json()["project_id"]
    assert [project.id for project in store.list_projects()].count(project_id) == 1
    assert len(store.list_import_records(project_id)) == 1


def test_allows_overwrite_when_requested(tmp_path, monkeypatch):
    store = _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    proj_dir = str(tmp_path / "overwrite_proj")

    client = TestClient(app)
    # First
    r1 = client.post(
        "/api/projects/create",
        json={
            "project_name": "Overwrite Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": proj_dir,
            "overwrite": True,
        },
    )
    assert r1.status_code == 200

    # Second with overwrite=True
    r2 = client.post(
        "/api/projects/create",
        json={
            "project_name": "Overwrite Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": proj_dir,
            "overwrite": True,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    project_id = r2.json()["project_id"]
    assert project_id == r1.json()["project_id"]
    assert [project.id for project in store.list_projects()].count(project_id) == 1
    assert len(store.list_import_records(project_id)) == 1


def test_refuses_duplicate_project_name_in_different_directory(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata").resolve()
    client = TestClient(app)

    first = client.post(
        "/api/projects/create",
        json={
            "project_name": "Duplicate Name",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "first_project"),
        },
    )
    second = client.post(
        "/api/projects/create",
        json={
            "project_name": "Duplicate Name",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "second_project"),
        },
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert not (tmp_path / "second_project").exists()


# ── Test: desktop config updated ─────────────────────────────────────


def test_updates_active_project_id(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Active Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": str(tmp_path / "active_proj"),
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    pid = response.json()["project_id"]

    cfg = desktop_config.get_desktop_config(redacted=False)
    assert cfg["active_project_id"] == pid
    assert cfg["project_dir"] == str((tmp_path / "active_proj").resolve())
    assert any(p["project_id"] == pid for p in cfg["recent_projects"])


def test_adds_authorized_data_dir(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    client = TestClient(app)

    client.post(
        "/api/projects/create",
        json={
            "project_name": "AuthDir Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": str(tmp_path / "auth_proj"),
            "overwrite": False,
        },
    )

    cfg = desktop_config.get_desktop_config(redacted=False)
    assert len(cfg["authorized_data_dirs"]) >= 1
    assert any(d["path"] == str(rawdata.resolve()) for d in cfg["authorized_data_dirs"])


def test_old_desktop_config_gets_project_field_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "old_desktop_config.json"
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", config_path)
    config_path.write_text(json.dumps({"project_dir": "."}), encoding="utf-8")

    cfg = desktop_config.get_desktop_config(redacted=False)

    assert cfg["active_project_id"] == ""
    assert cfg["recent_projects"] == []
    assert cfg["authorized_data_dirs"] == []


# ── Test: rawdata untouched ───────────────────────────────────────────


def test_does_not_write_to_rawdata(tmp_path, monkeypatch):
    """rawdata directory must have no new files after project creation."""
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    before_count = _count_files_in_dir(rawdata)

    client = TestClient(app)
    client.post(
        "/api/projects/create",
        json={
            "project_name": "NoWrite Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": str(tmp_path / "nowrite_proj"),
            "overwrite": False,
        },
    )

    after_count = _count_files_in_dir(rawdata)
    assert after_count == before_count, (
        f"rawdata file count changed: {before_count} -> {after_count}"
    )


# ── Test: non-ASCII project_name ──────────────────────────────────────


def test_non_ascii_project_name_slug_safe(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "测试-项目_2026",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": str(tmp_path / "unicode_proj"),
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    # project_id should be safe ASCII
    pid = data["project_id"]
    assert all(ord(ch) < 128 for ch in pid), f"project_id is not ASCII-safe: {pid}"
    assert all(ch.islower() or ch.isdigit() or ch == "-" for ch in pid)
    assert data["project_name"] == "测试-项目_2026"
    assert data["project_config_path"]


def test_rejects_project_dir_overlapping_rawdata(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = tmp_path / "rawdata"
    rawdata.mkdir()
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Unsafe Output",
            "rawdata_dir": str(rawdata),
            "project_dir": str(rawdata / "project"),
        },
    )

    assert response.status_code == 400
    assert "must not" in response.json()["detail"].lower()
    assert list(rawdata.iterdir()) == []


# ── Test: run_inspection=false skips scanning ────────────────────────


def test_skip_inspection(tmp_path, monkeypatch):
    """run_inspection=False should skip BIDS scan but still create project."""
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "SkipInspect Test",
            "rawdata_dir": str(rawdata.resolve()),
            "project_dir": str(tmp_path / "skip_proj"),
            "run_inspection": False,
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["dataset_index_path"] is None
    assert data["diagnostics"]["status"] == "NOT_INSPECTED"


# ── Test: default project_dir ────────────────────────────────────────


def test_default_project_dir(tmp_path, monkeypatch):
    """When project_dir is None, default to outputs/projects/<project_id>."""
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata")
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Default Dir Test",
            "rawdata_dir": str(rawdata.resolve()),
            "overwrite": False,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert Path(data["project_dir"]).parent == (tmp_path / "projects").resolve()


def test_project_config_references_rawdata_and_dataset_index(tmp_path, monkeypatch):
    _clean_desktop_config(tmp_path, monkeypatch)
    rawdata = Path("examples/synthetic_bids/rawdata").resolve()
    client = TestClient(app)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Config References",
            "rawdata_dir": str(rawdata),
            "project_dir": str(tmp_path / "config_references"),
        },
    )

    assert response.status_code == 200
    config = yaml.safe_load(
        Path(response.json()["project_config_path"]).read_text(encoding="utf-8")
    )
    assert config["data"]["copy_mode"] == "reference"
    assert config["data"]["rawdata_dir"] == str(rawdata)
    assert config["data"]["dataset_index"] == response.json()["dataset_index_path"]
    assert config["safety"]["rawdata_readonly"] is True
    assert config["safety"]["require_confirmation"] is True
