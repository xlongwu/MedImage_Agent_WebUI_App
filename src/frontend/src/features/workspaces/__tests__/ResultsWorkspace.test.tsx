import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { ResultsWorkspace } from "../ResultsWorkspace";

vi.mock("../../../components/ArtifactBrowser", () => ({
  ArtifactBrowser: ({ projectId }: { projectId?: string | null }) => (
    <div data-testid="artifact-browser">Artifact browser {projectId}</div>
  ),
}));

describe("ResultsWorkspace", () => {
  it("shows only the read-only project artifact browser", () => {
    render(<ResultsWorkspace baseUrl="http://localhost" projectId="project-1" />);

    expect(
      screen.getByRole("region", { name: "Artifact browser and image viewer" }),
    ).toHaveTextContent("Artifact browser project-1");
    expect(screen.getByText("No preview selected")).toBeInTheDocument();
    expect(screen.queryByText(/report exporter/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/group summary/i)).not.toBeInTheDocument();
  });

  it("requires a project before showing artifacts", () => {
    render(
      <I18nProvider locale="zh-CN">
        <ResultsWorkspace baseUrl="http://localhost" projectId={null} />
      </I18nProvider>,
    );

    expect(screen.getByText("检查结果前请选择项目")).toBeInTheDocument();
    expect(screen.queryByTestId("artifact-browser")).not.toBeInTheDocument();
  });
});
