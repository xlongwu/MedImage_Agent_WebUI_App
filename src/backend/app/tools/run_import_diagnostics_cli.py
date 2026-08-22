from __future__ import annotations

import argparse
from pathlib import Path

from src.backend.app.api.dashboard_routes import (
    create_dataset_diagnostics_package,
    get_latest_dataset_diagnostics_package,
    import_dataset,
    verify_dataset_diagnostics_package,
)
from src.backend.app.api.dependencies import ProjectStore, get_project_store
from src.backend.app.schemas.desktop import DatasetImportRequest
from src.backend.app.tools.cli_utils import emit_json_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate or verify Import Diagnostics handoff packages.")
    parser.add_argument("--project-id", default="brain-tumor-study", help="Project ID to inspect.")
    parser.add_argument("--import-path", default="", help="Optional dataset path to register before running the mode.")
    parser.add_argument(
        "--dataset-type",
        choices=["auto", "nifti", "dicom", "bids"],
        default="auto",
        help="Dataset type for --import-path. auto infers from visible files.",
    )
    parser.add_argument(
        "--mode",
        choices=["status", "package", "verify", "all"],
        default="status",
        help="status reads latest package, package generates a package, verify checks ZIP checksums, all does package+verify.",
    )
    return parser


def infer_dataset_type(path: str) -> str:
    root = Path(path)
    if (root / "dataset_description.json").is_file():
        return "bids"
    suffixes = {item.suffix.lower() for item in root.rglob("*") if item.is_file()}
    if ".dcm" in suffixes or ".ima" in suffixes:
        return "dicom"
    if ".nii" in suffixes or ".gz" in suffixes:
        return "nifti"
    return "bids"


def maybe_register_import(
    project_id: str,
    import_path: str,
    dataset_type: str,
    *,
    store: ProjectStore,
) -> dict | None:
    if not import_path:
        return None
    path = Path(import_path)
    if not path.exists():
        return {
            "success": False,
            "message": f"Import path does not exist: {import_path}",
            "path": import_path,
        }
    resolved_type = infer_dataset_type(import_path) if dataset_type == "auto" else dataset_type
    response = import_dataset(
        DatasetImportRequest(project_id=project_id, path=str(path), type=resolved_type),  # type: ignore[arg-type]
        store=store,
    )
    return response.model_dump()


def run(
    project_id: str,
    mode: str,
    import_path: str = "",
    dataset_type: str = "auto",
    *,
    store: ProjectStore | None = None,
) -> dict:
    active_store = store or get_project_store()
    import_response = maybe_register_import(
        project_id,
        import_path,
        dataset_type,
        store=active_store,
    )
    if import_response and not import_response.get("success"):
        return {"ok": False, "project_id": project_id, "import": import_response}
    if mode == "status":
        payload = get_latest_dataset_diagnostics_package(
            project_id=project_id,
            store=active_store,
        ).model_dump()
        if import_response:
            payload["import"] = import_response
        return payload
    if mode == "package":
        payload = create_dataset_diagnostics_package(
            project_id=project_id,
            store=active_store,
        ).model_dump()
        if import_response:
            payload["import"] = import_response
        return payload
    if mode == "verify":
        payload = verify_dataset_diagnostics_package(
            project_id=project_id,
            store=active_store,
        ).model_dump()
        if import_response:
            payload["import"] = import_response
        return payload
    if mode == "all":
        package = create_dataset_diagnostics_package(
            project_id=project_id,
            store=active_store,
        )
        verification = verify_dataset_diagnostics_package(
            project_id=project_id,
            store=active_store,
        )
        payload = {
            "ok": package.ok and verification.ok,
            "project_id": project_id,
            "package": package.model_dump(),
            "verification": verification.model_dump(),
        }
        if import_response:
            payload["import"] = import_response
        return payload
    raise ValueError(f"Unsupported mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run(project_id=args.project_id, mode=args.mode, import_path=args.import_path, dataset_type=args.dataset_type)
    return emit_json_result(payload, failure_code=2)


if __name__ == "__main__":
    raise SystemExit(main())
