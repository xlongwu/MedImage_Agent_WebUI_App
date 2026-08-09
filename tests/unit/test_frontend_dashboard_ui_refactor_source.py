from __future__ import annotations

import os

ROOT = os.getcwd()


def _read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def _read_en_messages() -> str:
    return _read("src/frontend/src/i18n/messages/en.ts")


def test_app_shell_workspace_structure_exists():
    app = _read("src/frontend/src/App.tsx")
    shell = _read("src/frontend/src/features/app/AppShellView.tsx")
    chrome = _read("src/frontend/src/features/dashboard/DashboardChrome.tsx")
    app_shell = _read("src/frontend/src/layouts/AppShell/AppShell.tsx")
    project_shell = _read("src/frontend/src/layouts/ProjectShell/ProjectShell.tsx")
    app_components = ["AppShellView"]
    shell_components = [
        "AppShell",
        "ProjectShell",
        "TopBar",
        "ProjectsPage",
        "ProjectCreateSheet",
        "AgentWorkspace",
        "OverviewWorkspace",
        "RunActivityBar",
        "ContextInspector",
        "AssistantSheet",
        "MedicalImageViewer",
        "DataConversionWorkspace",
        "PlanWorkspace",
        "PreprocessingWorkspace",
        "QCReportsWorkspace",
    ]
    chrome_components = [
        "TopBar",
        "WorkspaceHeader",
        "WorkspaceSuspenseFallback",
    ]
    for component in app_components:
        assert component in app, f"{component} must be wired from App.tsx"
    navigation = _read("src/frontend/src/features/navigation/workspaceModel.ts")
    assert "locationForProject" in navigation and 'workspace: "agent"' in navigation
    for component in shell_components:
        assert component in shell, f"{component} must exist in AppShellView"
    for component in chrome_components:
        assert component in shell or component in chrome, (
            f"{component} must exist in AppShellView or DashboardChrome"
        )
    assert "topBarSlot" in app_shell
    assert "lifecycleSlot" in app_shell
    assert "inspectorSlot" in app_shell
    assert "runActivitySlot" in app_shell
    assert 'workspaceId = "workflow-workspace"' in project_shell


def test_advanced_preprocessing_placeholder_text_exists():
    panel = _read("src/frontend/src/components/AdvancedPreprocessingPipelinePanel.tsx")
    messages = _read_en_messages()
    assert "technical.AdvancedPreprocessingPipeline.001" in panel
    assert "technical.AdvancedPreprocessingPipeline.002" in panel
    assert "Preprocessing validation" in messages
    assert (
        "Create a preprocessing run after conversion or BIDS registration to inspect the full pipeline."
        in messages
    )


def test_raw_dicom_and_bids_expected_wording_exists():
    workspace = _read("src/frontend/src/features/workspaces/DataConversionWorkspace.tsx")
    bids = _read("src/frontend/src/components/BidsValidationPanel.tsx")
    nifti = _read("src/frontend/src/components/NiftiQcSnapshotPanel.tsx")
    data_readiness = _read("src/frontend/src/components/DataReadinessPanel.tsx")
    dashboard_chrome = _read("src/frontend/src/features/dashboard/DashboardChrome.tsx")
    project_create_panel = _read("src/frontend/src/features/app/ProjectCreateResultPanel.tsx")
    combined = (
        workspace
        + bids
        + nifti
        + data_readiness
        + dashboard_chrome
        + project_create_panel
        + _read_en_messages()
    )
    assert "Raw DICOM candidates" in combined
    assert "Converted subjects" in combined
    messages = _read_en_messages()
    assert 't("data.bids.rawExpectedDescription")' in bids
    assert 't("technical.NiftiQcSnapshot.notApplicableDescription")' in nifti
    assert "NIfTI QC is not applicable until DICOM data is converted." in messages
    assert (
        "BIDS validation is expected to be incomplete before DICOM-to-NIfTI conversion." in messages
    )


def test_next_actions_cleanup_helper_exists():
    component = _read("src/frontend/src/components/dashboardUi.tsx")
    model = _read("src/frontend/src/components/dashboardUiModel.ts")
    assert "cleanupNextActions" in component and "cleanupNextActions" in model
    assert "normalizeActionText" in model
    assert "rawDicomPriority" in model


def test_app_shell_does_not_render_technical_tools_as_default_cards():
    shell = _read("src/frontend/src/features/app/AppShellView.tsx")
    plan_workspace = _read("src/frontend/src/features/workspaces/PlanWorkspace.tsx")
    preprocessing_workspace = _read(
        "src/frontend/src/features/workspaces/PreprocessingWorkspace.tsx"
    )
    assert "SecondaryToolsDrawer" not in shell
    assert "CompactTaskLog" not in shell
    assert "PlanReviewConsole" not in shell
    assert "SpmRealignDryRunPanel" not in shell
    assert "SpmRealignWrapperSkeletonPanel" not in shell
    assert "EnvironmentHealthPanel" not in shell
    assert "PlanReviewConsole" in plan_workspace
    assert "TechnicalModuleSection" in preprocessing_workspace


def test_advanced_preprocessing_mounts_once_and_not_in_review_panel():
    workspace = _read("src/frontend/src/features/workspaces/PreprocessingWorkspace.tsx")
    review = _read("src/frontend/src/components/DicomConversionReviewPanel.tsx")
    assert workspace.count("<AdvancedPreprocessingPipelinePanel") == 1
    assert "AdvancedPreprocessingPipelinePanel" not in review


def test_no_forbidden_execution_or_classification_text():
    checked_paths = [
        "src/frontend/src/App.tsx",
        "src/frontend/src/components/AdvancedPreprocessingPipelinePanel.tsx",
        "src/frontend/src/components/dashboardUi.tsx",
        "src/frontend/src/lib/api/legacy.ts",
    ]
    forbidden = [
        "Run Full Preprocessing",
        "Run DPABI",
        "Run Group Statistics",
        "Run Classification",
        "Train Classifier",
        "Clinical Diagnosis",
    ]
    for path in checked_paths:
        content = _read(path)
        for text in forbidden:
            assert text not in content, f"{text!r} found in {path}"


def test_api_paths_remain_present():
    paths_to_check = [
        "src/frontend/src/lib/api/dicom.ts",
        "src/frontend/src/lib/api/preprocessing.ts",
        "src/frontend/src/lib/api/legacy_re_exports.ts",
    ]
    combined = ""
    for p in paths_to_check:
        combined += _read(p)
    expected_paths = [
        "/api/projects/${encodeURIComponent(projectId)}/data-readiness",
        "/api/projects/${encodeURIComponent(projectId)}/bids-validation",
        "/api/projects/${encodeURIComponent(projectId)}/conversion/dry-run",
        "/api/projects/${encodeURIComponent(projectId)}/conversion/preflight",
    ]
    for path in expected_paths:
        assert path in combined, f"{path} not found in API source files"


def test_no_advanced_panel_auto_execution():
    panel = _read("src/frontend/src/components/AdvancedPreprocessingPipelinePanel.tsx")
    assert "useEffect" not in panel
    assert "handleValidation();" not in panel
    assert "handleReport();" not in panel
