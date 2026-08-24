import type { ComponentProps } from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProjectsPage } from "../ProjectsPage";
import type { ProjectSummary } from "../../../lib/types/project";
import { I18nProvider } from "../../../i18n/I18nProvider";

function project(
  id: string,
  name: string,
  overrides: Partial<ProjectSummary> = {},
): ProjectSummary {
  return {
    id,
    name,
    study_id: id.toUpperCase(),
    modality: "rs-fMRI",
    created_date: "June 13, 2026",
    subjects_count: 12,
    current_pipeline_id: "not-selected",
    ...overrides,
  };
}

function renderPage(overrides: Partial<ComponentProps<typeof ProjectsPage>> = {}) {
  const props: ComponentProps<typeof ProjectsPage> = {
    deletingProjectId: null,
    error: "",
    loading: false,
    onClose: vi.fn(),
    onCreateProject: vi.fn(),
    onDeleteProject: vi.fn(),
    onSelectProject: vi.fn(),
    projects: [
      project("p1", "Raw Study"),
      project("p2", "QC Cohort", {
        modality: "MRI / DWI",
        subjects_count: 8,
        latest_agent_task: {
          task_id: "task-2",
          state: "completed",
          outcome: "succeeded",
          goal_summary: "Run FC",
          current_action: "Task completed",
          current_action_code: "completed",
          requires_user: false,
          result_title: "FC outputs are ready",
          recent_activity: "Task completed",
          updated_at: "2026-06-14T10:00:00Z",
        },
      }),
    ],
    selectedProjectId: "p1",
    ...overrides,
  };

  render(
    <I18nProvider locale="en">
      <ProjectsPage {...props} />
    </I18nProvider>,
  );
  return props;
}

describe("ProjectsPage", () => {
  it("renders project metrics and filters the project list", async () => {
    const user = userEvent.setup();
    renderPage();

    expect(screen.getByRole("heading", { name: "Recent Projects" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /raw study/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /qc cohort/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Completed" }));

    expect(screen.queryByRole("heading", { name: /raw study/i })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /qc cohort/i })).toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: /search projects/i }));
    await user.type(screen.getByRole("searchbox", { name: /search projects/i }), "nothing");

    expect(screen.getByText("No projects match the current filters.")).toBeInTheDocument();
  });

  it("selects a project and returns to the workspace", async () => {
    const user = userEvent.setup();
    const props = renderPage();
    const row = screen.getByRole("heading", { name: /qc cohort/i }).closest("article");

    await user.click(within(row as HTMLElement).getByRole("button", { name: "Select" }));

    expect(props.onSelectProject).toHaveBeenCalledWith("p2");
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("uses the backend attention flag and shows the latest result summary", async () => {
    const user = userEvent.setup();
    renderPage({
      projects: [
        project("p-canceled", "Canceled cohort", {
          latest_agent_task: {
            task_id: "task-canceled",
            state: "needs_attention",
            outcome: "canceled",
            goal_summary: "Run preprocessing",
            current_action: "Task canceled",
            current_action_code: "attention",
            requires_user: true,
            result_title: "Execution was canceled",
            recent_activity: "Task canceled",
            updated_at: "2026-06-15T10:00:00Z",
          },
        }),
      ],
    });

    expect(screen.getByText("Execution was canceled")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Needs attention" }));
    expect(screen.getByRole("heading", { name: /canceled cohort/i })).toBeInTheDocument();
  });

  it("requires confirmation before removing a project listing", async () => {
    const user = userEvent.setup();
    const props = renderPage();
    const row = screen.getByRole("heading", { name: /raw study/i }).closest("article");

    await user.click(within(row as HTMLElement).getByRole("button", { name: "Remove" }));

    expect(screen.getByRole("dialog", { name: "Remove project" })).toHaveTextContent(
      "Data on disk is preserved",
    );
    expect(props.onDeleteProject).not.toHaveBeenCalled();

    await user.click(
      within(screen.getByRole("dialog", { name: "Remove project" })).getByRole("button", {
        name: "Remove",
      }),
    );

    expect(props.onDeleteProject).toHaveBeenCalledWith("p1", "Raw Study");
  });

  it("shows the voxel-grid empty state when no projects exist", async () => {
    const user = userEvent.setup();
    const props = renderPage({ projects: [] });

    await user.click(screen.getByRole("button", { name: /create your first research project/i }));

    expect(props.onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("shows a project-list unavailable state without fallback rows after load errors", async () => {
    const user = userEvent.setup();
    const props = renderPage({ error: "backend offline", projects: [] });

    expect(screen.getByText("Project list unavailable")).toBeInTheDocument();
    expect(screen.getByText(/backend offline/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/raw study/i)).not.toBeInTheDocument();

    const addProjectButtons = screen.getAllByRole("button", { name: "New Project" });
    await user.click(addProjectButtons[addProjectButtons.length - 1]);

    expect(props.onCreateProject).toHaveBeenCalledTimes(1);
  });

  it("shows backend warnings above verified rows without adding fallback projects", () => {
    renderPage({
      error: "partial timeout",
      projects: [
        project("p1", "Verified Study", {
          latest_agent_task: {
            task_id: "task-1",
            state: "running",
            outcome: null,
            goal_summary: "Run preprocessing",
            current_action: "Executing processing",
            current_action_code: "executing",
            requires_user: false,
            result_title: null,
            recent_activity: "Executing processing",
            updated_at: "2026-06-14T10:00:00Z",
          },
        }),
      ],
    });

    expect(screen.getByRole("status")).toHaveTextContent("partial timeout");
    expect(screen.getByRole("heading", { name: /verified study/i })).toBeInTheDocument();
    expect(screen.getByText("Executing the approved workflow")).toBeInTheDocument();
    expect(screen.queryByText("Raw Study")).not.toBeInTheDocument();
    expect(screen.queryByText("QC Cohort")).not.toBeInTheDocument();
  });
});
