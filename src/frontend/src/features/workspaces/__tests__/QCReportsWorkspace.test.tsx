import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getLatestQcDashboardReport } from "../../../lib/api/qc";
import { I18nProvider } from "../../../i18n/I18nProvider";
import {
  getProjectBoldReferenceReadiness,
  getProjectMotionQcReadiness,
  getProjectNiftiQcSnapshot,
  getLatestNativeFullPreprocessingRun,
} from "../../../lib/api/preprocessing";
import type {
  BoldReferenceReadinessResponse,
  MotionQcReadinessResponse,
  NativeFullPreprocResponse,
  NiftiQcSnapshotResponse,
  QcDashboardReportResponse,
} from "../../../types";
import { QCReportsWorkspace } from "../QCReportsWorkspace";

vi.mock("../../../lib/api/qc", () => ({
  getLatestQcDashboardReport: vi.fn(),
}));

vi.mock("../../../lib/api/preprocessing", () => ({
  getProjectBoldReferenceReadiness: vi.fn(),
  getProjectMotionQcReadiness: vi.fn(),
  getProjectNiftiQcSnapshot: vi.fn(),
  getLatestNativeFullPreprocessingRun: vi.fn(),
}));

vi.mock("../../../components/NiftiQcSnapshotPanel", () => ({
  default: () => <div data-testid="nifti-qc-snapshot-panel">NIfTI QC snapshot panel</div>,
}));

vi.mock("../../../components/BoldReferenceReadinessPanel", () => ({
  default: () => (
    <div data-testid="bold-reference-readiness-panel">BOLD reference readiness panel</div>
  ),
}));

vi.mock("../../../components/MotionQcReadinessPanel", () => ({
  default: () => <div data-testid="motion-qc-readiness-panel">Motion QC readiness panel</div>,
}));

function renderWorkspace(projectId: string | null = "project-1") {
  render(<QCReportsWorkspace baseUrl="http://localhost" projectId={projectId} />);
}

const latestQcDashboardMock = vi.mocked(getLatestQcDashboardReport);
const niftiSnapshotMock = vi.mocked(getProjectNiftiQcSnapshot);
const boldReadinessMock = vi.mocked(getProjectBoldReferenceReadiness);
const motionReadinessMock = vi.mocked(getProjectMotionQcReadiness);
const latestNativeRunMock = vi.mocked(getLatestNativeFullPreprocessingRun);

function niftiSnapshot(overrides: Partial<NiftiQcSnapshotResponse> = {}): NiftiQcSnapshotResponse {
  return {
    ok: true,
    project_id: "project-1",
    status: "ready",
    checked_at: "2026-07-04T00:00:00Z",
    image_count: 0,
    readable_count: 0,
    unreadable_count: 0,
    four_d_count: 0,
    warning_count: 0,
    images: [],
    warnings: [],
    errors: [],
    next_actions: [],
    safety_flags: { read_only: true },
    ...overrides,
  };
}

function boldReadiness(
  overrides: Partial<BoldReferenceReadinessResponse> = {},
): BoldReferenceReadinessResponse {
  return {
    ok: true,
    project_id: "project-1",
    status: "ready",
    checked_at: "2026-07-04T00:00:00Z",
    candidate_count: 0,
    ready_count: 0,
    warning_count: 0,
    blocked_count: 0,
    candidates: [],
    warnings: [],
    errors: [],
    next_actions: [],
    safety_flags: { read_only: true },
    ...overrides,
  };
}

function motionReadiness(
  overrides: Partial<MotionQcReadinessResponse> = {},
): MotionQcReadinessResponse {
  return {
    ok: true,
    project_id: "project-1",
    status: "ready",
    checked_at: "2026-07-04T00:00:00Z",
    candidate_count: 0,
    candidates: [],
    missing_motion_param_count: 0,
    fd_available_count: 0,
    warnings: [],
    errors: [],
    next_actions: [],
    safety_flags: { read_only: true },
    ...overrides,
  };
}

function qcDashboardReport(
  overrides: Partial<QcDashboardReportResponse> = {},
): QcDashboardReportResponse {
  return {
    ok: true,
    project_id: "project-1",
    status: "warning",
    generated_at: "2026-07-04T00:00:00Z",
    report_dir: "/tmp/qc",
    json_path: "/tmp/qc/qc_dashboard_report.json",
    markdown_path: "/tmp/qc/qc_dashboard_report.md",
    artifacts: [],
    modules: [],
    ready_count: 4,
    warning_count: 1,
    blocked_count: 0,
    unknown_count: 0,
    overall_warnings: [],
    overall_errors: [],
    next_actions: [],
    safety_flags: { read_only: true },
    ...overrides,
  };
}

function nativeRun(overrides: Partial<NativeFullPreprocResponse> = {}): NativeFullPreprocResponse {
  return {
    ok: true,
    status: "succeeded",
    dry_run: false,
    project_id: "project-1",
    run_id: "run-native",
    run_dir: "/tmp/native",
    backend: "native_python",
    stage_graph: [],
    stage_results: [
      {
        stage_id: "motion_qc",
        display_name: "Motion QC",
        node_id: "native_motion_qc",
        status: "succeeded",
        capability_level: "computed",
        validation_status: "synthetic_tested_reference_pending",
        backend: "native_python",
        input_artifacts: [],
        output_artifacts: [{ artifact_type: "motion_qc", path: "motion.json" }],
        warnings: [],
        errors: [],
        blocking_issues: [],
        validation_errors: [],
        result: {},
      },
      {
        stage_id: "realignment",
        display_name: "Realignment",
        node_id: "native_realign",
        status: "simplified",
        capability_level: "computed",
        validation_status: "synthetic_tested_reference_pending",
        backend: "native_python",
        input_artifacts: [],
        output_artifacts: [{ artifact_type: "mean_functional", path: "mean.nii.gz" }],
        warnings: [],
        errors: [],
        blocking_issues: [],
        validation_errors: [],
        result: {},
      },
      {
        stage_id: "functional_connectivity",
        display_name: "Functional connectivity",
        node_id: "native_fc",
        status: "succeeded",
        capability_level: "computed",
        validation_status: "synthetic_tested_reference_pending",
        backend: "native_python",
        input_artifacts: [],
        output_artifacts: [{ artifact_type: "fc_matrix", path: "fc.tsv" }],
        warnings: [],
        errors: [],
        blocking_issues: [],
        validation_errors: [],
        result: {},
      },
    ],
    completed_stages: ["motion_qc", "functional_connectivity"],
    blocked_stages: [],
    failed_stages: [],
    skipped_stages: [],
    metadata_only_stages: [],
    warning_stages: [],
    artifact_count: 3,
    manifest_path: "/tmp/native/manifest.json",
    validation_report_path: "/tmp/native/validation.json",
    final_report_path: "/tmp/native/report.json",
    warnings: [],
    errors: [],
    blocking_issues: [],
    next_actions: [],
    safety_flags: { no_external_tools_executed: true },
    ...overrides,
  };
}

describe("QCReportsWorkspace", () => {
  beforeEach(() => {
    latestQcDashboardMock.mockReset();
    niftiSnapshotMock.mockReset();
    boldReadinessMock.mockReset();
    motionReadinessMock.mockReset();
    latestNativeRunMock.mockReset();
    latestQcDashboardMock.mockRejectedValue(new Error("404"));
    niftiSnapshotMock.mockResolvedValue(niftiSnapshot());
    boldReadinessMock.mockResolvedValue(boldReadiness());
    motionReadinessMock.mockResolvedValue(motionReadiness());
    latestNativeRunMock.mockRejectedValue(new Error("404"));
  });

  it("renders project selection and module gates in simplified Chinese", () => {
    render(
      <I18nProvider locale="zh-CN">
        <QCReportsWorkspace baseUrl="http://localhost" projectId={null} />
      </I18nProvider>,
    );

    expect(screen.getByText("质量控制复核前请选择项目")).toBeInTheDocument();
    expect(screen.queryByLabelText("详细质量控制模块")).not.toBeInTheDocument();
  });

  it("renders conservative QC evidence states in simplified Chinese", async () => {
    render(
      <I18nProvider locale="zh-CN">
        <QCReportsWorkspace baseUrl="http://localhost" projectId="project-1" />
      </I18nProvider>,
    );

    expect(screen.getByRole("heading", { name: "证据优先质量控制面板" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("质量控制摘要状态")).toHaveTextContent(
        "没有来源证据时不作通过／失败结论",
      ),
    );
    expect(screen.getByLabelText("质量控制异常值重点区域")).toHaveTextContent("运动异常值");
    expect(screen.getByLabelText("影像对比产物门控")).toHaveTextContent("没有可用对比产物");
    expect(screen.getByLabelText("质量控制可视化要求")).toHaveTextContent("数据范围");
  });

  it("shows a unified QC dashboard before detailed modules", async () => {
    renderWorkspace();

    expect(
      screen.getByRole("heading", { name: "Evidence-first QC dashboard" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("QC summary states")).toHaveTextContent("Evidence");
    await waitFor(() =>
      expect(screen.getByLabelText("QC summary states")).toHaveTextContent("No pass/fail decision"),
    );
    expect(screen.getByRole("table", { name: "Subject-level QC status" })).toHaveTextContent(
      "Subject rows appear only after dashboard reports",
    );
    expect(screen.getByLabelText("QC outlier focus areas")).toHaveTextContent("Motion outliers");
    expect(screen.getByLabelText("Image comparison artifact gate")).toHaveTextContent(
      "No comparison artifact is available",
    );
    expect(screen.getByLabelText("Image comparison artifact states")).toHaveTextContent(
      "Partial artifact",
    );
    expect(screen.queryByRole("button", { name: "Sync slices" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Opacity locked" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Before / after" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "QC chart contract" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Visualization contract" })).toBeInTheDocument();
    expect(screen.getByLabelText("QC visualization requirements")).toHaveTextContent("Unit");
    expect(screen.getByLabelText("QC visualization requirements")).toHaveTextContent("Threshold");
    expect(screen.getByLabelText("QC visualization requirements")).toHaveTextContent("Data range");
    expect(screen.getByLabelText("QC visualization requirements")).toHaveTextContent("Drill-down");
    expect(screen.getByLabelText("Detailed QC modules")).toBeInTheDocument();
  });

  it("summarizes loaded native and QC evidence instead of leaving the overview empty", async () => {
    latestQcDashboardMock.mockResolvedValue(qcDashboardReport({ warning_count: 2 }));
    niftiSnapshotMock.mockResolvedValue(
      niftiSnapshot({
        image_count: 6,
        readable_count: 6,
        four_d_count: 3,
        warning_count: 1,
        images: [
          {
            image_id: "sub-001-bold",
            path: "/tmp/sub-001_task-rest_bold.nii.gz",
            subject_id: "sub-001",
            modality: "bold",
            suffix: "bold",
            exists: true,
            readable: true,
            dimensions: [64, 64, 33, 240],
            voxel_spacing: [3, 3, 3.6],
            nan_count: 0,
            warnings: [] as string[],
          },
          {
            image_id: "sub-001-derived-mask",
            path: "/tmp/sub-001/anat/sub-001_T1w_desc-coregistered_t1w_desc-brainMask.nii.gz",
            subject_id: "sub-001_T1w_desc-coregistered_t1w_desc-brainMask.nii.gz",
            modality: "anat",
            suffix: "mask",
            exists: true,
            readable: true,
            dimensions: [64, 64, 33],
            voxel_spacing: [3, 3, 3.6],
            nan_count: 0,
            warnings: [],
          },
        ],
      }),
    );
    boldReadinessMock.mockResolvedValue(
      boldReadiness({
        candidate_count: 1,
        ready_count: 1,
        candidates: [
          {
            subject_id: "sub-001",
            bold_path: "/tmp/sub-001_task-rest_bold.nii.gz",
            dimensions: [64, 64, 33, 240],
            voxel_spacing: [3, 3, 3.6],
            volume_count: 240,
            is_4d: true,
            has_sidecar: true,
            repetition_time: 2,
            has_slice_timing: true,
            reference_strategy: "middle_volume",
            warnings: [],
          },
        ],
      }),
    );
    motionReadinessMock.mockResolvedValue(
      motionReadiness({
        candidate_count: 1,
        fd_available_count: 1,
        candidates: [
          {
            subject_id: "sub-001",
            bold_path: "/tmp/sub-001_task-rest_bold.nii.gz",
            has_sidecar: true,
            has_motion_params: true,
            motion_param_paths: ["/tmp/rp_sub-001.txt"],
            has_fd_column: true,
            warnings: [],
          },
        ],
      }),
    );
    latestNativeRunMock.mockResolvedValue(nativeRun());

    renderWorkspace();

    await waitFor(() =>
      expect(screen.getByLabelText("QC summary states")).toHaveTextContent(
        "Backend evidence loaded",
      ),
    );
    expect(screen.getByLabelText("QC summary states")).toHaveTextContent("1 subject(s)");
    expect(screen.getByRole("table", { name: "Subject-level QC status" })).toHaveTextContent(
      "sub-001",
    );
    expect(screen.getByRole("table", { name: "Subject-level QC status" })).toHaveTextContent(
      "FD available",
    );
    expect(
      screen.queryByText("sub-001_T1w_desc-coregistered_t1w_desc-brainMask.nii.gz"),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("QC outlier focus areas")).toHaveTextContent(
      "1 subject(s) FD ready",
    );
    expect(screen.getByLabelText("QC outlier focus areas")).toHaveTextContent(
      "Native spatial artifacts",
    );
    expect(screen.getByLabelText("QC outlier focus areas")).toHaveTextContent("Partial artifact");
    expect(screen.getByLabelText("Image comparison artifact gate")).toHaveTextContent(
      "Spatial artifacts are available",
    );
    expect(screen.getByText("FC computed")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Outlier drill-down evidence" })).toHaveTextContent(
      "sub-001",
    );
    expect(screen.getByRole("table", { name: "Outlier drill-down evidence" })).toHaveTextContent(
      "/tmp/rp_sub-001.txt",
    );
    expect(
      screen.queryByText(/Subject rows appear only after dashboard reports/),
    ).not.toBeInTheDocument();
  });

  it("surfaces backend evidence failures and retries the real API requests", async () => {
    latestQcDashboardMock.mockReset();
    latestQcDashboardMock
      .mockRejectedValueOnce(new Error("backend offline"))
      .mockResolvedValue(qcDashboardReport());

    renderWorkspace();

    expect(await screen.findByRole("alert")).toHaveTextContent("backend offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(latestQcDashboardMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("aggregates a completed native batch by subject instead of counting duplicate BOLD candidates", async () => {
    const subjects = ["sub-001", "sub-002", "sub-003"];
    const stage = (
      subjectId: string,
      stageId: string,
      status: "succeeded" | "simplified" | "warning",
      artifactCount: number,
    ): NativeFullPreprocResponse["stage_results"][number] => ({
      stage_id: stageId,
      display_name: stageId,
      node_id: `native_${stageId}`,
      status,
      capability_level: status === "simplified" ? "simplified" : "computed",
      validation_status: "synthetic_tested_reference_pending",
      backend: "native_python",
      input_artifacts: [],
      output_artifacts: Array.from({ length: artifactCount }, (_, index) => ({
        artifact_type: `${stageId}_${index}`,
        path: `/tmp/native/${subjectId}/${stageId}_${index}.nii.gz`,
      })),
      warnings: status === "warning" || status === "simplified" ? [`${stageId}_warning`] : [],
      errors: [],
      blocking_issues: [],
      validation_errors: [],
      result: { subject_id: subjectId },
    });
    const stageResults = subjects.flatMap((subjectId) => [
      stage(subjectId, "motion_qc", "succeeded", 3),
      stage(subjectId, "realignment", "simplified", 2),
      stage(subjectId, "normalization", "simplified", 4),
      stage(subjectId, "alff", "warning", 1),
      stage(subjectId, "falff", "warning", 1),
      stage(subjectId, "reho", "succeeded", 1),
      stage(subjectId, "functional_connectivity", "succeeded", 2),
    ]);
    latestQcDashboardMock.mockResolvedValue(qcDashboardReport({ warning_count: 0 }));
    niftiSnapshotMock.mockResolvedValue(niftiSnapshot());
    boldReadinessMock.mockResolvedValue(
      boldReadiness({
        candidate_count: 6,
        ready_count: 6,
        warning_count: 6,
        candidates: subjects.flatMap((subjectId) =>
          ["bold", "bolda"].map((suffix) => ({
            subject_id: subjectId,
            bold_path: `/tmp/${subjectId}_task-rest_${suffix}.nii.gz`,
            dimensions: [64, 64, 33, 240],
            voxel_spacing: [3, 3, 3.6],
            volume_count: 240,
            is_4d: true,
            has_sidecar: true,
            repetition_time: 2,
            has_slice_timing: true,
            reference_strategy: "middle_volume" as const,
            warnings: ["TaskName is missing from sidecar."],
          })),
        ),
      }),
    );
    motionReadinessMock.mockResolvedValue(
      motionReadiness({
        candidate_count: 6,
        fd_available_count: 6,
        candidates: subjects.flatMap((subjectId) =>
          ["bold", "bolda"].map((suffix) => ({
            subject_id: subjectId,
            bold_path: `/tmp/${subjectId}_task-rest_${suffix}.nii.gz`,
            has_sidecar: true,
            has_motion_params: true,
            motion_param_paths: [`/tmp/native/${subjectId}/fd.tsv`],
            has_fd_column: true,
            warnings: [] as string[],
          })),
        ),
      }),
    );
    latestNativeRunMock.mockResolvedValue(
      nativeRun({
        stage_results: stageResults,
        warning_stages: subjects.flatMap((subjectId) =>
          ["realignment", "normalization", "alff", "falff"].map(
            (stageId) => `${subjectId}:${stageId}`,
          ),
        ),
      }),
    );

    renderWorkspace();

    await waitFor(() =>
      expect(screen.getByLabelText("QC summary states")).toHaveTextContent("3 subject(s)"),
    );
    expect(screen.getByLabelText("QC summary states")).toHaveTextContent("15");
    expect(screen.getByLabelText("QC outlier focus areas")).toHaveTextContent(
      "3 subject(s) FD ready",
    );
    expect(screen.getByLabelText("Image comparison artifact gate")).toHaveTextContent(
      "3 subject(s) have paired",
    );
    expect(screen.getByText("Computed with warnings")).toBeInTheDocument();
    expect(screen.getByText("Range: 3 subject(s), 6 FC artifact(s)")).toBeInTheDocument();
    for (const subjectId of subjects) {
      expect(screen.getByRole("table", { name: "Subject-level QC status" })).toHaveTextContent(
        subjectId,
      );
    }
  });

  it("updates the overview from partial evidence while a separate source is still loading", async () => {
    latestQcDashboardMock.mockReturnValue(new Promise<QcDashboardReportResponse>(() => {}));
    niftiSnapshotMock.mockResolvedValue(
      niftiSnapshot({
        image_count: 1,
        readable_count: 1,
        four_d_count: 1,
        images: [
          {
            image_id: "sub-001-bold",
            path: "/tmp/sub-001_task-rest_bold.nii.gz",
            subject_id: "sub-001",
            modality: "bold",
            suffix: "bold",
            exists: true,
            readable: true,
            dimensions: [64, 64, 33, 240],
            voxel_spacing: [3, 3, 3.6],
            nan_count: 0,
            warnings: [],
          },
        ],
      }),
    );
    boldReadinessMock.mockResolvedValue(
      boldReadiness({
        candidate_count: 1,
        ready_count: 1,
        candidates: [
          {
            subject_id: "sub-001",
            bold_path: "/tmp/sub-001_task-rest_bold.nii.gz",
            dimensions: [64, 64, 33, 240],
            voxel_spacing: [3, 3, 3.6],
            volume_count: 240,
            is_4d: true,
            has_sidecar: true,
            repetition_time: 2,
            has_slice_timing: true,
            reference_strategy: "middle_volume",
            warnings: [],
          },
        ],
      }),
    );
    motionReadinessMock.mockResolvedValue(
      motionReadiness({
        candidate_count: 1,
        fd_available_count: 1,
        candidates: [
          {
            subject_id: "sub-001",
            bold_path: "/tmp/sub-001_task-rest_bold.nii.gz",
            has_sidecar: true,
            has_motion_params: true,
            motion_param_paths: ["/tmp/rp_sub-001.txt"],
            has_fd_column: true,
            warnings: [],
          },
        ],
      }),
    );
    latestNativeRunMock.mockResolvedValue(nativeRun());

    renderWorkspace();

    await waitFor(() =>
      expect(screen.getByLabelText("QC summary states")).toHaveTextContent(
        "Backend evidence loaded",
      ),
    );
    expect(screen.getByRole("table", { name: "Subject-level QC status" })).toHaveTextContent(
      "sub-001",
    );
    expect(screen.getByLabelText("Image comparison artifact gate")).toHaveTextContent(
      "Spatial artifacts are available",
    );
    expect(screen.getByText("FC computed")).toBeInTheDocument();
  });

  it("keeps only read-only QC evidence panels available for selected projects", () => {
    renderWorkspace();

    expect(screen.getByTestId("nifti-qc-snapshot-panel")).toBeInTheDocument();
    expect(screen.getByTestId("bold-reference-readiness-panel")).toBeInTheDocument();
    expect(screen.getByTestId("motion-qc-readiness-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("motion-metrics-draft-panel")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rsfmri-qc-planning-report-panel")).not.toBeInTheDocument();
  });

  it("does not render detailed QC modules until a project is selected", () => {
    renderWorkspace(null);

    expect(screen.getByText("Select a project before QC review")).toBeInTheDocument();
    expect(screen.queryByLabelText("Detailed QC modules")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("table", { name: "Subject-level QC status" }),
    ).not.toBeInTheDocument();
  });

  it("removes legacy derived metric execution panels from ordinary QC", () => {
    renderWorkspace();

    expect(screen.queryByTestId("nuisance-regression-panel")).not.toBeInTheDocument();

    expect(screen.queryByRole("button", { name: "Open derived modules" })).not.toBeInTheDocument();
  });
});
