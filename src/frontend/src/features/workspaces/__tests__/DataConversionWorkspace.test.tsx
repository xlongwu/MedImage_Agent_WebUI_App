import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProjectInventory } from "../../../lib/projectWorkflow";
import { DataConversionWorkspace } from "../DataConversionWorkspace";

vi.mock("../../../lib/api/dicom", () => ({
  getLatestConversionDryRun: vi.fn().mockRejectedValue(new Error("not found")),
}));

vi.mock("../../../components/BidsValidationPanel", () => ({
  default: () => <div>BIDS validation</div>,
}));

vi.mock("../../../components/DataReadinessPanel", () => ({
  default: () => <div>Data readiness</div>,
}));

const rawInventory: ProjectInventory = {
  projectName: "Demo Project",
  modality: "rs-fMRI",
  dataState: "raw_dicom",
  dataStateLabel: "Raw DICOM",
  stateSentence: "Raw DICOM is available.",
  rawDicomCandidates: 1,
  dicomSeriesCount: 2,
  dicomFileCount: 100,
  convertedSubjects: 0,
  niftiFileCount: 0,
  hasRawDicom: true,
  hasConvertedData: false,
  metadataOnlyNiftiInventory: false,
};

describe("DataConversionWorkspace", () => {
  it("keeps conversion evidence read-only and routes a new conversion to Agent", () => {
    const onOpenAgent = vi.fn();
    render(
      <DataConversionWorkspace
        baseUrl="http://localhost"
        inventory={rawInventory}
        onOpenAgent={onOpenAgent}
        projectId="project-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Agent workspace" }));

    expect(onOpenAgent).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /generate.*dry-run/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/DICOM Conversion Review/i)).not.toBeInTheDocument();
  });
});
