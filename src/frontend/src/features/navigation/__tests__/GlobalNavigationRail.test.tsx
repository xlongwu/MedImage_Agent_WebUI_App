import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../../../i18n/I18nProvider";
import { GlobalNavigationRail } from "../GlobalNavigationRail";

describe("GlobalNavigationRail", () => {
  it("exposes only Projects, Agent, Runs, and Settings", async () => {
    const user = userEvent.setup();
    const onOpenWorkspace = vi.fn();
    render(
      <I18nProvider locale="en">
        <GlobalNavigationRail
          location={{ kind: "project", projectId: "project-1", workspace: "agent" }}
          onOpenProjects={vi.fn()}
          onOpenWorkspace={onOpenWorkspace}
          projectId="project-1"
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: "Agent" })).toHaveAttribute("aria-current", "page");
    await user.click(screen.getByRole("button", { name: "Runs" }));
    expect(onOpenWorkspace).toHaveBeenCalledWith("project-1", "runs");

    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Overview" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button")).toHaveLength(4);
  });

  it("keeps project workspaces disabled until a project is selected", () => {
    render(
      <I18nProvider locale="en">
        <GlobalNavigationRail
          location={{ kind: "projects" }}
          onOpenProjects={vi.fn()}
          onOpenWorkspace={vi.fn()}
          projectId={null}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole("button", { name: "Projects" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Agent" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Settings" })).toBeDisabled();
  });
});
