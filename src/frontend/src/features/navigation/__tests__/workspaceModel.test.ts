import { describe, expect, it } from "vitest";

import { locationForProject, projectWorkspaces } from "../workspaceModel";

describe("workspaceModel", () => {
  it("opens every selected project at the Agent workspace", () => {
    expect(locationForProject("project-1")).toEqual({
      kind: "project",
      projectId: "project-1",
      workspace: "agent",
    });
    expect(projectWorkspaces).toEqual(["agent", "runs", "settings"]);
  });

  it("exposes only the three project workspaces behind the Projects entry", () => {
    expect(projectWorkspaces).toEqual(["agent", "runs", "settings"]);
  });
});
