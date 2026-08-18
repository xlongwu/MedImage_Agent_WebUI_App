import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProjectInventory } from "../../../lib/projectWorkflow";
import type { ConversionDryRunResponse } from "../../../types";
import { DicomSeriesTable } from "../DicomSeriesTable";

function inventory(overrides: Partial<ProjectInventory> = {}): ProjectInventory {
  return {
    projectName: "Demo Project",
    modality: "rs-fMRI",
    dataState: "raw_dicom",
    dataStateLabel: "Raw DICOM",
    stateSentence: "Raw DICOM data detected.",
    rawDicomCandidates: 20,
    dicomSeriesCount: 80,
    dicomFileCount: 12000,
    convertedSubjects: 0,
    niftiFileCount: 0,
    hasRawDicom: true,
    hasConvertedData: false,
    metadataOnlyNiftiInventory: false,
    ...overrides,
  };
}

function dryRunWithMappings(count: number): ConversionDryRunResponse {
  return {
    ok: true,
    project_id: "project-1",
    status: "ready",
    dry_run: true,
    checked_at: "2026-06-25T00:00:00Z",
    target_layout: "bids",
    output_root_name: "rawdata",
    output_root_preview: "D:\\study\\rawdata",
    source_summaries: [
      {
        source_id: "source-1",
        source_type: "dicom",
        root: "D:\\study\\dicom",
        exists: true,
        file_count: 12000,
        subject_candidates: ["sub-001", "sub-002"],
        series_count: count,
        warnings: [] as string[],
      },
    ],
    mapping_preview: Array.from({ length: count }, (_, index) => {
      const padded = String(index).padStart(3, "0");

      return {
        source_path: `D:\\study\\dicom\\sub-${padded}\\REST`,
        source_series_uid: `series-${padded}`,
        source_type: "dicom_series",
        subject_id: `sub-${padded}`,
        session_id: "ses-01",
        modality: "func",
        suffix: "bold",
        task: "rest",
        suggested_relative_path: `sub-${padded}/ses-01/func/sub-${padded}_ses-01_task-rest_bold.nii.gz`,
        confidence: "high" as const,
        warnings: [] as string[],
      };
    }),
    blocking_issues: [],
    warnings: [],
    next_actions: ["Review mapping preview."],
    safety_flags: { dry_run_only: true },
  };
}

describe("DicomSeriesTable", () => {
  it("summarizes manual-review mappings and keeps row selection local", () => {
    const dryRun = dryRunWithMappings(2);
    dryRun.status = "warning";
    dryRun.mapping_preview[0].confidence = "low";
    const onReviewSelectionChange = vi.fn();

    render(
      <DicomSeriesTable
        dryRun={dryRun}
        error=""
        inventory={inventory()}
        loading={false}
        onReviewSelectionChange={onReviewSelectionChange}
        projectId="project-1"
      />,
    );

    expect(screen.getByLabelText("Dry-run review summary")).toHaveTextContent("warning");
    expect(screen.getByLabelText("Dry-run review summary")).toHaveTextContent("Manual review");
    expect(screen.getAllByText("Low confidence mapping requires manual review.")).toHaveLength(2);

    fireEvent.click(screen.getByLabelText("Select series-000"));

    expect(screen.getByLabelText("Selected DICOM sources")).toHaveTextContent(
      "review selection only",
    );
    expect(screen.getByLabelText("Selected DICOM sources")).toHaveTextContent(
      "sub-000 - func / bold / task-rest - series-000 - Series UID",
    );
    expect(screen.getByLabelText("Selected DICOM sources")).toHaveTextContent("does not execute");
    expect(onReviewSelectionChange).toHaveBeenLastCalledWith(
      expect.objectContaining({
        evidenceLevel: "preview_only",
        series: "series-000",
        status: "low",
        subject: "sub-000",
      }),
    );

    fireEvent.click(screen.getByLabelText("Select series-000"));

    expect(onReviewSelectionChange).toHaveBeenLastCalledWith(null);
  });

  it("uses DICOM series filter semantics and distinguishes duplicate selection chips", () => {
    const dryRun = dryRunWithMappings(2);
    dryRun.mapping_preview[0].source_series_uid = "shared-series";
    dryRun.mapping_preview[1].source_series_uid = "shared-series";
    dryRun.mapping_preview[0].modality = "func";
    dryRun.mapping_preview[0].suffix = "bold";
    dryRun.mapping_preview[1].modality = "anat";
    dryRun.mapping_preview[1].suffix = "T1w";

    render(
      <DicomSeriesTable
        dryRun={dryRun}
        error=""
        inventory={inventory()}
        loading={false}
        projectId="project-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "DICOM series" }));

    expect(screen.getByRole("table", { name: /2 visible row/i })).toHaveTextContent(
      "shared-series",
    );

    fireEvent.click(screen.getAllByLabelText("Select shared-series")[0]);
    fireEvent.click(screen.getAllByLabelText("Select shared-series")[1]);

    const selection = screen.getByLabelText("Selected DICOM sources");
    expect(selection).toHaveTextContent("sub-000 - func / bold / task-rest - shared-series");
    expect(selection).toHaveTextContent("sub-001 - anat / T1w / task-rest - shared-series");
  });

  it("virtualizes large DICOM source tables while preserving total row context", async () => {
    render(
      <DicomSeriesTable
        dryRun={dryRunWithMappings(80)}
        error=""
        inventory={inventory()}
        loading={false}
        projectId="project-1"
      />,
    );

    expect(screen.getByRole("table", { name: /80 visible row/i })).toHaveAttribute(
      "aria-rowcount",
      "80",
    );
    expect(screen.getByRole("status")).toHaveTextContent("Rendering rows 1-");
    expect(screen.getByText("series-000")).toBeInTheDocument();
    expect(screen.queryByText("series-079")).not.toBeInTheDocument();

    const viewport = screen.getByLabelText("Virtualized DICOM series table");
    viewport.scrollTop = 9999;
    fireEvent.scroll(viewport);

    await waitFor(() => expect(screen.getByText("series-079")).toBeInTheDocument());
    expect(screen.queryByText("series-000")).not.toBeInTheDocument();
  });
});
