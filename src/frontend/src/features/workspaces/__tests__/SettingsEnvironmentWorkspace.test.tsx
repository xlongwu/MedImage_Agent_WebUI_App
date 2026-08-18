import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SettingsEnvironmentWorkspace } from "../SettingsEnvironmentWorkspace";
import { I18nProvider } from "../../../i18n/I18nProvider";

vi.mock("../../../components/EnvironmentHealthPanel", () => ({
  default: () => <div data-testid="environment-health-panel">Environment health panel</div>,
}));

vi.mock("../../memory/MemorySettingsPanel", () => ({
  MemorySettingsPanel: () => <div data-testid="memory-settings-panel">Memory settings panel</div>,
}));

function renderWorkspace(locale: "en" | "zh-CN" = "en", advancedMode = false) {
  const onThemePreferenceChange = vi.fn();
  const onAdvancedModeChange = vi.fn();

  render(
    <I18nProvider locale={locale}>
      <SettingsEnvironmentWorkspace
        advancedMode={advancedMode}
        baseUrl="http://localhost"
        localePreference={locale}
        onLocalePreferenceChange={vi.fn()}
        onAdvancedModeChange={onAdvancedModeChange}
        onThemePreferenceChange={onThemePreferenceChange}
        projectId="project-1"
        themePreference="light"
      />
    </I18nProvider>,
  );

  return { onAdvancedModeChange, onThemePreferenceChange };
}

describe("SettingsEnvironmentWorkspace", () => {
  it("shows the settings map and safety gates without legacy execution tools", () => {
    renderWorkspace();

    expect(screen.getByRole("navigation", { name: "Settings domains" })).toHaveTextContent(
      "Diagnostics",
    );
    expect(screen.getByRole("heading", { name: "Settings map" })).toBeInTheDocument();
    expect(screen.getByTestId("memory-settings-panel")).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Settings domains" })).toHaveTextContent("Safety");
    expect(screen.getByRole("heading", { name: "General and integrations" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Safety gates" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Safety policy matrix" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Environment setup modules")).not.toBeInTheDocument();
  });

  it("routes theme preference changes through app state", () => {
    const { onThemePreferenceChange } = renderWorkspace();

    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));

    expect(onThemePreferenceChange).toHaveBeenCalledWith("dark");
  });

  it("keeps Advanced Mode off and warns before opt-in", () => {
    const { onAdvancedModeChange } = renderWorkspace();

    fireEvent.click(screen.getByRole("radio", { name: "On" }));

    expect(onAdvancedModeChange).toHaveBeenCalledWith(true);
  });

  it("shows only read-only environment readiness in Advanced Mode", () => {
    renderWorkspace("en", true);

    expect(screen.getByTestId("environment-health-panel")).toBeInTheDocument();
    expect(screen.queryByText(/SPM realign dry-run/i)).not.toBeInTheDocument();
    expect(screen.queryByTestId("external-smoke-panel")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("System diagnostics modules")).not.toBeInTheDocument();
  });

  it("renders the settings map and safety policies in Chinese", () => {
    renderWorkspace("zh-CN");

    expect(screen.getByRole("heading", { name: "设置与环境" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "设置域" })).toHaveTextContent("诊断");
    expect(screen.getByRole("table", { name: "安全策略矩阵" })).toHaveTextContent("rawdata 只读");
  });
});
