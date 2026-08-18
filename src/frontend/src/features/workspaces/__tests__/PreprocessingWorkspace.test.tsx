import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import type { ProjectInventory } from "../../../lib/projectWorkflow";
import { PreprocessingWorkspace } from "../PreprocessingWorkspace";

const inventory: ProjectInventory = {
  projectName: "Demo Project",
  modality: "rs-fMRI",
  dataState: "converted_bids",
  dataStateLabel: "Converted BIDS/NIfTI",
  stateSentence: "Converted BIDS/NIfTI data is available.",
  rawDicomCandidates: 0,
  dicomSeriesCount: 0,
  dicomFileCount: 0,
  convertedSubjects: 4,
  niftiFileCount: 24,
  hasRawDicom: false,
  hasConvertedData: true,
  metadataOnlyNiftiInventory: false,
};

describe("PreprocessingWorkspace", () => {
  it("keeps raw DICOM blocked with only a data-conversion route", () => {
    const onOpenDataConversion = vi.fn();
    render(
      <PreprocessingWorkspace
        baseUrl="http://localhost"
        dataState="raw_dicom"
        hasPreprocessingRun={false}
        inventory={{ ...inventory, dataState: "raw_dicom", hasConvertedData: false }}
        onOpenDataConversion={onOpenDataConversion}
        onOpenToolsDrawer={vi.fn()}
        projectId="project-1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Return to Data & Conversion" }));
    expect(onOpenDataConversion).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Run native dry-run")).not.toBeInTheDocument();
  });

  it("shows stage information but routes execution setup to the Agent", () => {
    const onOpenAgent = vi.fn();
    render(
      <I18nProvider locale="en">
        <PreprocessingWorkspace
          baseUrl="http://localhost"
          dataState="converted_bids"
          hasPreprocessingRun={false}
          inventory={inventory}
          onOpenAgent={onOpenAgent}
          onOpenDataConversion={vi.fn()}
          onOpenToolsDrawer={vi.fn()}
          projectId="project-1"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("list", { name: "Preprocessing stages" })).toHaveTextContent(
      "Data preparation",
    );
    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(onOpenAgent).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Execute native pipeline")).not.toBeInTheDocument();
  });
});
