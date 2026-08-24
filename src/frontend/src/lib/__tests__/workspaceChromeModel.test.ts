import { describe, expect, it } from "vitest";

import { workspaceChromePresetForLocation } from "../workspaceChromeModel";

describe("workspaceChromePresetForLocation", () => {
  it("maps core routes to stable shell presets", () => {
    expect(workspaceChromePresetForLocation({ kind: "projects" })).toBe("project-library");
    expect(
      workspaceChromePresetForLocation({
        kind: "project",
        projectId: "p1",
        workspace: "agent",
      }),
    ).toBe("project-dashboard");
    expect(
      workspaceChromePresetForLocation({
        kind: "project",
        projectId: "p1",
        workspace: "runs",
      }),
    ).toBe("task-workspace");
    expect(
      workspaceChromePresetForLocation({
        kind: "project",
        projectId: "p1",
        workspace: "settings",
      }),
    ).toBe("standard-workspace");
  });
});
