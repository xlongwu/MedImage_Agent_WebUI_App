import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../i18n/I18nProvider";
import {
  getProjectBoldReferenceReadiness,
  getProjectMotionQcReadiness,
  getProjectNiftiQcSnapshot,
} from "../../lib/api/preprocessing";
import BoldReferenceReadinessPanel from "../BoldReferenceReadinessPanel";
import MotionQcReadinessPanel from "../MotionQcReadinessPanel";
import NiftiQcSnapshotPanel from "../NiftiQcSnapshotPanel";

vi.mock("../../lib/api/preprocessing", () => ({
  getProjectBoldReferenceReadiness: vi.fn(),
  getProjectMotionQcReadiness: vi.fn(),
  getProjectNiftiQcSnapshot: vi.fn(),
  getProjectNiftiThumbnail: vi.fn(),
}));

describe("QC readiness i18n", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getProjectMotionQcReadiness).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      status: "ready",
      checked_at: "2026-07-26T00:00:00Z",
      candidate_count: 2,
      candidates: [],
      missing_motion_param_count: 0,
      fd_available_count: 2,
      warnings: [],
      errors: [],
      next_actions: [
        "FD column available for 2 BOLD candidate(s) across 2 subject(s). Motion QC computation can proceed.",
        "Motion QC data is ready. Generate a preprocessing plan in the Plan Review Console.",
      ],
      safety_flags: {},
    });
    vi.mocked(getProjectBoldReferenceReadiness).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      status: "ready",
      checked_at: "2026-07-26T00:00:00Z",
      candidate_count: 2,
      ready_count: 2,
      warning_count: 0,
      blocked_count: 0,
      candidates: [],
      warnings: [],
      errors: [],
      next_actions: ["2 BOLD candidate(s) are ready for reference planning."],
      safety_flags: {},
    });
    vi.mocked(getProjectNiftiQcSnapshot).mockResolvedValue({
      ok: true,
      project_id: "project-1",
      status: "ready",
      checked_at: "2026-07-26T00:00:00Z",
      image_count: 1,
      readable_count: 1,
      unreadable_count: 0,
      four_d_count: 1,
      warning_count: 0,
      images: [
        {
          image_id: "sub-001-bold",
          path: "rawdata/sub-001/func/sub-001_task-rest_bold.nii.gz",
          relative_path: "sub-001/func/sub-001_task-rest_bold.nii.gz",
          subject_id: "sub-001",
          modality: "BOLD",
          suffix: "bold",
          exists: true,
          readable: true,
          dimensions: [16, 16, 16, 10],
          volume_count: 10,
          voxel_spacing: [1, 1, 1],
          nan_count: 0,
          warnings: [],
        },
      ],
      warnings: [],
      errors: [],
      next_actions: [],
      safety_flags: { read_only: true },
    });
  });

  it("renders readiness metrics, statuses, and stable next actions in Chinese", async () => {
    render(
      <I18nProvider locale="zh-CN">
        <MotionQcReadinessPanel projectId="project-1" />
        <BoldReferenceReadinessPanel projectId="project-1" />
      </I18nProvider>,
    );

    expect((await screen.findAllByText("就绪")).length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText("READY")).not.toBeInTheDocument();
    expect(screen.getByText("候选文件")).toBeInTheDocument();
    expect(screen.getByText("缺少运动参数")).toBeInTheDocument();
    expect(screen.getByText("FD 可用")).toBeInTheDocument();
    expect(screen.getByText(/2 个 BOLD 候选文件.*2 名受试者/)).toBeInTheDocument();
    expect(screen.getByText(/BOLD 候选文件已可用于参考图像规划/)).toBeInTheDocument();
    expect(screen.queryByText(/Motion QC computation can proceed/)).not.toBeInTheDocument();
  });

  it("renders NIfTI QC status, metrics, and detail controls in Chinese", async () => {
    render(
      <I18nProvider locale="zh-CN">
        <NiftiQcSnapshotPanel projectId="project-1" />
      </I18nProvider>,
    );

    expect(await screen.findByText("就绪")).toBeInTheDocument();
    expect(screen.getByText("可读取")).toBeInTheDocument();
    expect(screen.getByText("不可读取")).toBeInTheDocument();
    expect(screen.getByText("警告")).toBeInTheDocument();
    expect(screen.queryByText("READY")).not.toBeInTheDocument();

    const details = screen.getByRole("button", { name: "显示详情" });
    fireEvent.click(details);
    expect(screen.getByRole("button", { name: "隐藏详情" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "加载中央切片" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show details" })).not.toBeInTheDocument();
  });

  it("retains the NIfTI QC labels in English for the English locale", async () => {
    render(
      <I18nProvider locale="en">
        <NiftiQcSnapshotPanel projectId="project-1" />
      </I18nProvider>,
    );

    expect(await screen.findByText("READY")).toBeInTheDocument();
    expect(screen.getByText("Readable")).toBeInTheDocument();
    expect(screen.getByText("Unreadable")).toBeInTheDocument();
    expect(screen.getByText("Warnings")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show details" })).toBeInTheDocument();
  });
});
