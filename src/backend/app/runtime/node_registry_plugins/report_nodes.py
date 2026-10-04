"""Report Nodes registry plugin."""
from __future__ import annotations

from src.backend.app.runtime.node_registry_plugins.base import NodeRunner
from src.backend.app.tools.docs_inventory import build_docs_inventory
from src.backend.app.tools.release_readiness import build_release_readiness
from src.backend.app.tools.report_exporter import export_rsfmri_report_package
from src.backend.app.tools.report_package_validator import validate_rsfmri_report_package


def run_rsfmri_report_exporter_node(context, node):
    """Export rs-fMRI report package with checksums and safety manifest."""
    result = export_rsfmri_report_package(
        project_dir=str(context.project_config["project"]["root_dir"]),
        derivatives_dir=context.derivatives_dir,
        reports_dir=context.project_config.get("runtime", {}).get("report_dir", "./reports"),
        work_dir=context.work_dir,
        exports_dir=node.params.get("exports_dir", "./exports"),
        export_id=node.params.get("export_id"),
        include_subject_qc=bool(node.params.get("include_subject_qc", True)),
        include_metrics=bool(node.params.get("include_metrics", True)),
        include_fc=bool(node.params.get("include_fc", True)),
        include_contracts=bool(node.params.get("include_contracts", True)),
        include_pipeline_runs=bool(node.params.get("include_pipeline_runs", True)),
    )
    result["node_id"] = node.id
    return result


def run_rsfmri_report_package_validator_node(context, node):
    """Validate exported report package integrity."""
    result = validate_rsfmri_report_package(
        exports_dir=node.params.get("exports_dir", "./exports"),
        export_id=node.params.get("export_id"),
        package_dir=node.params.get("package_dir"),
        zip_path=node.params.get("zip_path"),
        strict=bool(node.params.get("strict", False)),
    )
    result["node_id"] = node.id
    return result


def run_project_release_readiness_node(context, node):
    """Check project release readiness against quality gates."""
    result = build_release_readiness()
    result["node_id"] = node.id
    return result


def run_docs_inventory_node(context, node):
    """Build documentation inventory for the project."""
    result = build_docs_inventory()
    result["node_id"] = node.id
    return result


REGISTRY: dict[str, NodeRunner] = {
    "rsfmri_report_exporter": run_rsfmri_report_exporter_node,
    "rsfmri_report_package_validator": run_rsfmri_report_package_validator_node,
    "project_release_readiness": run_project_release_readiness_node,
    "docs_inventory": run_docs_inventory_node,
}
