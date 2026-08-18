"""Frontend source tests for Phase 5O-UIClosure — advanced preprocessing panel + dashboard polish."""

from __future__ import annotations

import os
import re

import pytest

ROOT = os.getcwd()


def _read(*parts: str) -> str:
    path = os.path.join(ROOT, *parts)
    if not os.path.exists(path):
        pytest.skip(f"{path} not found")
    return open(path, encoding="utf-8").read()


def _read_api():
    return _read("src/frontend/src/lib/api/client.ts")


def _read_advanced_panel():
    return _read("src/frontend/src/components/AdvancedPreprocessingPipelinePanel.tsx")


def _read_review_panel():
    return _read("src/frontend/src/components/DicomConversionReviewPanel.tsx")


def _read_bids_panel():
    return _read("src/frontend/src/components/BidsValidationPanel.tsx")


def _read_app():
    return _read("src/frontend/src/App.tsx")


def _read_en_messages():
    return _read("src/frontend/src/i18n/messages/en.ts")


def _read_workspace_model():
    return _read("src/frontend/src/features/navigation/workspaceModel.ts")


def _read_workspace_navigation():
    return _read("src/frontend/src/features/navigation/useWorkspaceNavigation.ts")


def _read_app_shell():
    return _read("src/frontend/src/features/app/AppShellView.tsx")


def _read_data_conversion_workspace():
    return _read("src/frontend/src/features/workspaces/DataConversionWorkspace.tsx")


def _read_preprocessing_workspace():
    return _read("src/frontend/src/features/workspaces/PreprocessingWorkspace.tsx")


def _read_project_controller():
    return _read("src/frontend/src/features/projects/useProjectController.ts")


def _read_styles():
    return _read("src/frontend/src/styles.css")


def _read_project_workflow():
    return _read("src/frontend/src/lib/projectWorkflow.ts")


def _read_dashboard_chrome():
    return _read("src/frontend/src/features/dashboard/DashboardChrome.tsx")


def _read_project_switcher():
    return _read("src/frontend/src/features/dashboard/ProjectSwitcher.tsx")


def _read_project_switcher_styles():
    return _read("src/frontend/src/features/dashboard/ProjectSwitcher.module.css")


def _read_project_overview_header():
    # ProjectOverviewHeader was folded into the workspace architecture.
    return _read("src/frontend/src/features/workspaces/OverviewWorkspace.tsx") + _read_en_messages()


def _read_dicom_series_table():
    return _read("src/frontend/src/features/workspaces/DicomSeriesTable.tsx")


def _read_run_activity_bar():
    return _read("src/frontend/src/features/tasks/RunActivityBar.tsx")


def _read_viewer_styles():
    return _read("src/frontend/src/features/app/MedicalImageViewer.module.css")


def _read_legacy_api():
    return _read("src/frontend/src/lib/api/legacy.ts")


# ═══════════════════════════════════════════════════════════════════════
# Panel existence
# ═══════════════════════════════════════════════════════════════════════


def test_advanced_panel_exists():
    content = _read_advanced_panel()
    assert "AdvancedPreprocessingPipelinePanel" in content, "Panel must export default component"


def test_mounted_once_in_preprocessing_workspace():
    workspace = _read_preprocessing_workspace()
    review = _read_review_panel()
    assert "<AdvancedPreprocessingPipelinePanel" in workspace, (
        "Preprocessing workspace must mount the panel"
    )
    assert workspace.count("<AdvancedPreprocessingPipelinePanel") == 1, (
        "Panel must be mounted exactly once in PreprocessingWorkspace"
    )
    assert "AdvancedPreprocessingPipelinePanel" not in review, (
        "Review panel must not mount preprocessing validation"
    )


# ═══════════════════════════════════════════════════════════════════════
# API wrappers
# ═══════════════════════════════════════════════════════════════════════


def test_preprocessing_api_exposes_read_only_evidence_wrapper():
    content = _read("src/frontend/src/lib/api/preprocessing.ts")
    assert "getLatestNativeFullPreprocessingRun" in content
    assert "/preprocessing/native/runs/latest" in content


def test_preprocessing_api_does_not_expose_execution_wrapper():
    content = _read("src/frontend/src/lib/api/preprocessing.ts")
    assert "executeReviewed" not in content
    assert "/execute-reviewed" not in content


# ═══════════════════════════════════════════════════════════════════════
# UI text
# ═══════════════════════════════════════════════════════════════════════


def test_check_pipeline_validation_button_exists():
    content = _read_advanced_panel()
    assert "Check pipeline validation" in content, "Validation button text must exist"


def test_export_report_button_exists():
    content = _read_advanced_panel()
    assert "Export preprocessing pipeline report" in content, "Report export button must exist"


def test_stage_names_in_panel():
    content = _read_advanced_panel() + _read_en_messages()
    stages = [
        "Slice Timing",
        "Coregistration",
        "Smoothing",
        "Nuisance Regression",
        "Temporal Filtering",
        "ALFF/ReHo",
        "Functional Connectivity",
    ]
    for s in stages:
        terms = s.lower().replace("/", " ").split()
        assert all(term in content.lower() for term in terms), (
            f"Stage '{s}' must be represented in the localized UI catalog"
        )


def test_safety_copy_exists():
    content = _read_advanced_panel()
    assert "rawdata" in content.lower(), "Rawdata safety copy must exist"
    assert (
        "no preprocessing is executed" in content.lower() or "metadata-only" in content.lower()
    ), "No execution statement must exist"


def test_preprocessing_empty_state_copy_exists():
    panel = _read_advanced_panel()
    messages = _read_en_messages()
    assert 't("technical.AdvancedPreprocessingPipeline.002")' in panel
    assert (
        "Create a preprocessing run after conversion or BIDS registration to inspect the full pipeline."
        in messages
    )


# ═══════════════════════════════════════════════════════════════════════
# Forbidden text
# ═══════════════════════════════════════════════════════════════════════


def test_no_run_full_preprocessing():
    content = _read_advanced_panel()
    assert "Run Full Preprocessing" not in content, "No Run Full Preprocessing"


def test_no_run_dpabi():
    content = _read_advanced_panel()
    assert "Run DPABI" not in content, "No Run DPABI"


def test_no_run_group_statistics():
    for src in [_read_advanced_panel(), _read_api()]:
        assert "Run Group Statistics" not in src, "No Run Group Statistics"


def test_no_run_classification():
    for src in [_read_advanced_panel(), _read_api()]:
        assert "Run Classification" not in src, "No Run Classification"


def test_no_clinical_diagnosis():
    content = _read_advanced_panel()
    assert "Clinical Diagnosis" not in content, "No Clinical Diagnosis"
    assert "clinical diagnosis" not in content.lower(), "No clinical diagnosis"


def test_no_shell_true():
    content = _read_advanced_panel()
    assert "shell=True" not in content, "No shell=True"


def test_no_auto_execution():
    """Verify no useEffect/auto-call triggers execution."""
    content = _read_advanced_panel()
    assert "useEffect" not in content, "No auto-execution on mount (useEffect)"


# ═══════════════════════════════════════════════════════════════════════
# Phase 5 UX Refactor Tests
# ═══════════════════════════════════════════════════════════════════════


def test_project_state_helper_exists():
    workflow = _read_project_workflow()
    assert "export function deriveProjectWorkflowState" in workflow, (
        "deriveProjectWorkflowState helper must exist"
    )


def test_raw_dicom_state_exists():
    workflow = _read_project_workflow()
    assert "raw_dicom" in workflow, "raw_dicom state must exist"


def test_converted_bids_state_exists():
    workflow = _read_project_workflow()
    assert "converted_bids" in workflow, "converted_bids state must exist"


def test_default_tab_selection_logic_exists():
    model = _read_workspace_model()
    navigation = _read_workspace_navigation()
    assert "locationForProject" in model, (
        "Project selection must have a canonical workspace location"
    )
    assert "openWorkspace" in navigation and "setLocation" in navigation, (
        "Workspace navigation must update location state"
    )
    assert 'workspace: "agent"' in model, (
        "Newly opened projects must start from the Agent workspace"
    )


def test_created_project_is_optimistically_merged_into_sidebar():
    controller = _read_project_controller()
    assert "mergeCreatedProjectIntoList" in controller, (
        "Created projects must be mergeable into the sidebar list"
    )
    assert "mergeCreatedProjectIntoList(result, refreshed ?? projects.data)" in controller, (
        "Upload flow must show the created project in Recent projects immediately"
    )
    workflow = _read_project_workflow()
    assert re.search(
        r"return\s+\[\s*createdProject,\s*\.\.\.projects\.filter\(\(item\)\s*=>\s*item\.id\s*!==\s*result\.project_id\)",
        workflow,
        re.S,
    ), "Created project must be placed before existing projects and de-duplicated"


def test_recent_projects_can_be_removed_from_sidebar_without_file_delete():
    controller = _read_project_controller()
    switcher = _read_project_switcher()
    switcher_styles = _read_project_switcher_styles()
    projects_api = _read("src/frontend/src/lib/api/projects.ts")
    client_api = _read("src/frontend/src/lib/api/client.ts")
    assert "deleteProject" in controller, (
        "Recent project delete handler must call the project delete API"
    )
    assert "projectCreateLoading" in controller, (
        "Recent project delete action must have a loading guard"
    )
    assert "handleDeleteRequest" in switcher and "moreButton" in switcher_styles, (
        "Recent project rows need a delete control"
    )
    messages = _read_en_messages()
    assert 't("projects.switcher.removeDescription"' in switcher, (
        "Delete confirmation must use the localized safety message"
    )
    assert "data on disk is preserved" in messages, (
        "Delete confirmation must preserve rawdata safety boundary"
    )
    assert "deleteJson" in client_api and 'method: "DELETE"' in client_api, (
        "API client must support DELETE"
    )
    assert (
        "deleteJson<ProjectDeleteResponse>(`/api/projects/${encodeURIComponent(projectId)}`)"
        in projects_api
    )


def test_upload_uses_unique_project_names_and_no_silent_overwrite():
    controller = _read_project_controller()
    assert "uniqueProjectName" in controller, (
        "Upload flow must have a project-name de-duplication helper"
    )
    assert "getApiBaseUrl" in controller, "Upload flow must use the runtime backend URL"
    assert "overwrite: false" in controller, (
        "Upload flow must not silently overwrite an existing project"
    )
    assert "overwrite: true" not in controller, (
        "Upload flow must not hide duplicate-name uploads by overwriting"
    )
    assert "isProjectNameConflict" in controller, "Upload flow must handle duplicate-name conflicts"


def test_converted_bids_copy():
    workspace = _read_data_conversion_workspace()
    messages = _read_en_messages()
    assert 't("data.checkPreprocessing")' in workspace
    assert "Check preprocessing validation" in messages


def test_raw_dicom_copy():
    shell = _read_app_shell()
    workspace = _read_data_conversion_workspace()
    overview = _read_project_overview_header()
    series_table = _read_dicom_series_table()
    combined = shell + workspace + overview + series_table
    assert "Generate conversion dry-run" in combined or "Generate dry-run preview" in combined


def test_raw_dicom_preprocessing_placeholder():
    workspace = _read_preprocessing_workspace()
    messages = _read_en_messages()
    assert 't("preprocessing.blockedDescription")' in workspace
    assert "Complete data conversion before preprocessing validation" in messages


def test_tools_drawer_collapsed_by_default():
    controller = _read("src/frontend/src/features/app/useAppController.ts")
    shell = _read_app_shell()
    assert "drawerOpen" in controller
    assert "useState(false)" in controller or "useState<boolean>(false)" in controller
    assert "ContextInspector" in shell
    assert "drawerOpen ? (" in shell


def test_recent_activity_collapsed():
    run_activity = _read_run_activity_bar()
    shell = _read_app_shell()
    assert "RunActivityBar" in shell, "Run activity must be mounted from AppShellView"
    assert "return null" in run_activity, (
        "Run activity bar must be hidden when no active or failed tasks exist"
    )
    assert "setExpanded" in run_activity and "run-activity-drawer" in run_activity, (
        "Run activity details must be collapsed behind the drawer"
    )


def test_no_train_classifier():
    for src in [_read_app(), _read_advanced_panel(), _read_api()]:
        assert "Train Classifier" not in src, "No Train Classifier"
        assert "train_classifier" not in src.lower(), "No train_classifier"


def test_no_backend_api_path_changes():
    legacy = _read_legacy_api()
    dicom_path = "src/frontend/src/lib/api/dicom.ts"
    preprocessing_path = "src/frontend/src/lib/api/preprocessing.ts"
    if not os.path.exists(os.path.join(ROOT, dicom_path)):
        pytest.skip("dicom.ts not found")
    if not os.path.exists(os.path.join(ROOT, preprocessing_path)):
        pytest.skip("preprocessing.ts not found")
    dicom_content = _read(dicom_path)
    preprocessing_content = _read(preprocessing_path)
    assert "/api/projects" in (legacy + dicom_content + preprocessing_content)
    assert "getLatestNativeFullPreprocessingRun" in preprocessing_content
    assert "/preprocessing/native/runs/latest" in preprocessing_content


# State consistency polish tests
def test_converted_bids_tab_routing():
    workflow = _read_project_workflow()
    assert '"preprocessing"' in workflow and '"converted_bids"' in workflow, (
        "converted_bids must default to preprocessing"
    )


def test_raw_dicom_tab_routing():
    workflow = _read_project_workflow()
    assert '"data"' in workflow and '"raw_dicom"' in workflow, "raw_dicom must default to data tab"


def test_converted_bids_data_conversion_not_primary():
    workspace = _read_data_conversion_workspace()
    messages = _read_en_messages()
    assert 't("data.convertedModeNote")' in workspace
    assert "DICOM conversion is not the primary workflow" in messages


def test_raw_dicom_bids_expected_before_conversion():
    bids = _read_bids_panel()
    messages = _read_en_messages()
    assert 't("data.bids.expected")' in bids
    assert "Expected before conversion" in messages, "raw_dicom must expect conversion"


def test_demo_data_like_raw_dicom_priority_source():
    """DICOM evidence with absent converted evidence must route to raw_dicom."""
    workflow = _read_project_workflow()
    assert "hasRawDicomEvidence" in workflow, "Classifier must have explicit raw DICOM evidence"
    assert "convertedDataAbsent" in workflow, "Classifier must check converted evidence absence"
    assert "dicom_file_count" in workflow and "dicom_series_count" in workflow, (
        "DICOM count signals must be inspected"
    )
    assert "raw_dicom_candidate_subjects" in workflow, (
        "Raw DICOM candidate subject signal must be preserved"
    )
    assert re.search(
        r"if\s*\(\s*hasRawDicomEvidence\s*&&\s*convertedDataAbsent\s*\)\s*\{\s*return\s+\"raw_dicom\";",
        workflow,
        re.S,
    ), "Raw DICOM evidence must take priority when NIfTI/BIDS evidence is absent"


def test_metadata_only_does_not_prove_converted_bids():
    workflow = _read_project_workflow()
    assert "isMetadataOnlySignal" in workflow, "Metadata-only signals must be detected separately"
    assert re.search(
        r"const\s+hasConvertedSubjectEvidence\s*=\s*!metadataOnly",
        workflow,
    ), "Metadata-only inventory must not count as converted subject evidence"
    assert "metadataOnlyNiftiInventory" in workflow, (
        "Metadata-only state should be carried as a display note"
    )


def test_raw_dicom_primary_action_not_preprocessing_validation():
    shell = _read_app_shell()
    workspace = _read_data_conversion_workspace()
    overview = _read_project_overview_header()
    series_table = _read_dicom_series_table()
    combined = shell + workspace + overview + series_table
    assert "Generate conversion dry-run" in combined or "Generate dry-run preview" in combined, (
        "raw_dicom primary action must be conversion dry-run"
    )


def test_nifti_metric_stays_numeric_when_metadata_only():
    bids = _read_bids_panel()
    workspace = _read_preprocessing_workspace()
    assert "Metadata-only inventory" not in bids
    assert "Metadata-only inventory" not in workspace
    assert (
        "NIfTI inventory: metadata only" in bids or "niftiCount" in bids or "Metadata-" not in bids
    )


def test_real_converted_bids_evidence_still_classifies_converted():
    workflow = _read_project_workflow()
    assert "const hasRealConvertedData" in workflow
    assert (
        "niftiCount > 0" in workflow
        and "hasRealBidsRoots" in workflow
        and "hasConvertedSubjectEvidence" in workflow
    )
    assert re.search(
        r"if\s*\(\s*hasRealConvertedData\s*\)\s*\{\s*return\s+\"converted_bids\";",
        workflow,
        re.S,
    ), "Real NIfTI/BIDS evidence must still route to converted_bids"


def test_empty_project_recommended_action():
    workspace = _read_data_conversion_workspace()
    messages = _read_en_messages()
    assert 't("data.emptyTitle")' in workspace
    assert "Import dataset" in messages or "Import a BIDS/NIfTI dataset" in messages, (
        "empty projects must recommend import"
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 5O Dashboard Polish Tests
# ═══════════════════════════════════════════════════════════════════════


def test_generate_conversion_dry_run_wording():
    """'Generate conversion dry-run' must remain the primary recommended action for raw DICOM."""
    shell = _read_app_shell()
    workspace = _read_data_conversion_workspace()
    overview = _read_project_overview_header()
    series_table = _read_dicom_series_table()
    combined = shell + workspace + overview + series_table
    assert "Generate conversion dry-run" in combined or "Generate dry-run preview" in combined, (
        "Generate conversion dry-run must be present"
    )


def test_review_conversion_readiness_wording():
    """'Review conversion readiness' or 'Generate conversion dry-run' must remain as secondary action wording."""
    shell = _read_app_shell()
    workspace = _read_data_conversion_workspace()
    overview = _read_project_overview_header()
    series_table = _read_dicom_series_table()
    combined = shell + workspace + overview + series_table
    assert (
        "Review conversion readiness" in combined
        or "Generate conversion dry-run" in combined
        or "Generate dry-run preview" in combined
    ), "Conversion readiness or dry-run wording must be present"


def test_no_run_dicom_to_bids_conversion_unsafe_wording():
    """'Run DICOM-to-BIDS conversion' must not appear as a user-facing action button."""
    shell = _read_app_shell()
    review = _read_review_panel()
    assert "Run DICOM-to-BIDS conversion" not in shell, (
        "Unsafe 'Run DICOM-to-BIDS conversion' must not appear in AppShellView"
    )
    assert "Run DICOM-to-BIDS conversion" not in review, (
        "Unsafe wording must not appear in review panel"
    )


def test_show_technical_details_toggle_exists():
    """'Show technical details' toggle must exist in the review panel and preprocessing workspace."""
    review = _read_review_panel()
    workspace = _read_preprocessing_workspace()
    panel = _read_advanced_panel()
    messages = _read_en_messages()
    assert (
        "showTechDetails" in review
        and "technical.DicomConversionReview.action.showTechnicalDetails" in review
        and "Show technical details" in messages
    ), "Technical details toggle must exist in review panel"
    assert (
        "Show technical details" in workspace
        or "technical details" in workspace.lower()
        or "Show technical details" in panel
        or "technical details" in panel.lower()
    ), "Show technical details toggle must appear in preprocessing workspace or panel"


def test_expandable_approval_requirements():
    """Approval requirements must be behind an expandable/collapsible control."""
    review = _read_review_panel()
    assert "APPROVAL_CHECKLIST" in review or "approval" in review.lower(), (
        "Approval checklist must exist"
    )
    assert "CollapsibleDetails" in review or "<details" in review, (
        "Approval requirements must be in a collapsible component"
    )


def test_expandable_env_flags():
    """Env flags / missing_env_flags must be behind technical details toggle, not default-visible."""
    review = _read_review_panel()
    assert "missing_env_flags" in review, "Env flags must be referenced"
    assert "showTechDetails" in review, "Env flags must be behind tech details gate"


def test_expandable_mapping_preview():
    """DICOM mapping preview must be behind a collapsible."""
    review = _read_review_panel()
    assert "technical.DicomConversionReview.mappings.title" in review
    assert "DICOM mapping preview" in _read_en_messages(), "Mapping preview must exist"
    assert "CollapsibleDetails" in review or "<details" in review, (
        "Mapping preview must be in a collapsible component"
    )


def test_sidebar_project_name_truncation():
    """Project names in sidebar must use a truncating CSS class or title attribute."""
    switcher = _read_project_switcher()
    assert "title={item.name}" in switcher, (
        "ProjectSwitcher must expose full project names via title attribute"
    )
    assert "itemName" in switcher, "ProjectSwitcher must use the itemName truncation class"


def test_sidebar_project_name_css_truncation():
    """CSS must define truncation for project pill names."""
    styles = _read_project_switcher_styles()
    assert ".itemName" in styles, "ProjectSwitcher CSS must define itemName truncation"
    assert "text-overflow: ellipsis" in styles, "CSS must use ellipsis truncation"


def test_viewer_height_reduced():
    """Viewer card min-height must be at most 400px (reduced from 430px)."""
    styles = _read_viewer_styles()
    match = re.search(r"\.viewerCard\s*\{[^}]*min-height:\s*(\d+)px", styles)
    assert match is not None, "viewer-card must have a min-height"
    height = int(match.group(1))
    assert height <= 400, (
        f"viewer-card min-height should be ≤400px for compact view, got {height}px"
    )


def test_blocked_conversion_calm_styling():
    """Blocked state must use amber/warning tone, not large red panel."""
    review = _read_review_panel()
    assert "technical.DicomConversionReview.005" in review
    assert "blocked by safety gates" in _read_en_messages(), (
        "Must use calmer 'blocked by safety gates' wording"
    )
    review_css = os.path.join(
        ROOT, "src/frontend/src/components/DicomConversionReviewPanel.module.css"
    )
    has_amber = "#925400" in review or "rgba(255, 248, 236" in review
    if not has_amber and os.path.exists(review_css):
        css = open(review_css, encoding="utf-8").read()
        has_amber = "#925400" in css or "rgba(255, 248, 236" in css
    assert has_amber, "Blocked state must use amber/warning tone instead of full red"


def test_no_auto_execution_useeffect_in_app():
    """App.tsx must not add auto-execution useEffect calls."""
    app = _read_app()
    assert "runProjectDicomConversion(" not in app, (
        "No direct conversion execution in App.tsx useEffect"
    )


def test_conversion_blocked_count_visible():
    """Blocked state must show prerequisite count visibly."""
    review = _read_review_panel()
    assert "blocking_issues.length" in review, "Must show blocking issue count"
    assert "technical.DicomConversionReview.blocked.prerequisites" in review
    assert "prerequisite(s) missing" in _read_en_messages(), (
        "Must show 'prerequisite(s) missing' text"
    )


def test_review_persist_requires_preflight_mappings():
    """Review package persistence must not save an empty mapping package."""
    review = _read_review_panel()
    assert "canPersistReview" in review, "Review panel must compute whether mappings are available"
    assert "data.mapping_count > 0" in review, "Persistence must require at least one mapping"
    assert "disabled={persisting || !canPersistReview}" in review, (
        "Persist review package button must be disabled when mappings are absent"
    )
    assert "technical.DicomConversionReview.persist.mappingRequired" in review
    assert (
        "Run conversion preflight and review at least one mapping before saving."
        in _read_en_messages()
    ), "Empty mapping persistence guard must explain the next step"


def test_review_panel_has_no_mojibake_markers():
    """Visible review text must not contain Windows mojibake markers."""
    review = _read_review_panel()
    for marker in ("璺", "鈿", "鈥", "閳", "路", "\ufffd"):
        assert marker not in review, f"Mojibake marker {marker!r} must not appear in review panel"
