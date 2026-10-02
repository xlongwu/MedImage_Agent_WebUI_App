import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ComponentProps, ReactElement } from "react";
import { TopBar, WorkspaceSuspenseFallback } from "../DashboardChrome";
import { I18nProvider } from "../../../i18n/I18nProvider";

const defaultTopBarProps: ComponentProps<typeof TopBar> = {
  activePageLabel: "Data",
  apiError: "",
  health: true,
  locale: "en",
  onBackToProjects: vi.fn(),
  onLocaleChange: vi.fn(),
  onOpenAssistant: vi.fn(),
  onOpenInspector: vi.fn(),
  onRetry: vi.fn(),
  projectName: "Demo Project",
  version: "0.6.0-rc1",
  versionFromBackend: true,
};

function renderTopBar(element: ReactElement) {
  return render(<I18nProvider locale="en">{element}</I18nProvider>);
}

describe("TopBar", () => {
  it("shows project context and opens the inspector", async () => {
    const user = userEvent.setup();
    const openInspector = vi.fn();
    const openAssistant = vi.fn();
    renderTopBar(
      <TopBar
        {...defaultTopBarProps}
        health={true}
        apiError=""
        onRetry={vi.fn()}
        projectName="Demo Project"
        activePageLabel="Data & Conversion"
        onOpenAssistant={openAssistant}
        onOpenInspector={openInspector}
      />,
    );

    expect(screen.getByText("MedImage Agent")).toBeInTheDocument();
    expect(screen.getByText("Demo Project")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /advanced console/i })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: "Primary navigation" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Explain the current task" }));
    expect(openAssistant).toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Inspector" }));
    expect(openInspector).toHaveBeenCalled();
  });

  it("surfaces backend errors with retry", async () => {
    const user = userEvent.setup();
    const retry = vi.fn();
    renderTopBar(
      <TopBar
        {...defaultTopBarProps}
        health={false}
        apiError="Backend disconnected"
        onRetry={retry}
        projectName="Demo Project"
        activePageLabel="Plan"
        onOpenAssistant={vi.fn()}
        onOpenInspector={vi.fn()}
      />,
    );

    expect(screen.getByText("Backend disconnected")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Backend offline");
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(retry).toHaveBeenCalled();
  });

  it("copies backend health diagnostics from the error banner", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    renderTopBar(
      <TopBar
        {...defaultTopBarProps}
        health={false}
        apiError="Backend disconnected"
        onRetry={vi.fn()}
        projectName="Demo Project"
        activePageLabel="Plan"
        onOpenAssistant={vi.fn()}
        onOpenInspector={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Copy diagnostics" }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Health: Backend offline"));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Project: Demo Project"));
    expect(screen.getByText("Diagnostics copied")).toBeInTheDocument();
  });
});

describe("WorkspaceSuspenseFallback", () => {
  it("renders a page-level skeleton fallback with status semantics", () => {
    render(<WorkspaceSuspenseFallback label="Loading workspace..." />);

    expect(screen.getByRole("status", { name: "Loading workspace..." })).toHaveTextContent(
      "Loading workspace...",
    );
    expect(screen.getByText("Loading workspace...")).toBeInTheDocument();
  });
});
