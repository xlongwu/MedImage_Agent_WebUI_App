import type { AppLocation } from "../features/navigation/workspaceModel";

export type WorkspaceChromePreset =
  | "project-library"
  | "project-dashboard"
  | "image-workspace"
  | "task-workspace"
  | "standard-workspace";

export function workspaceChromePresetForLocation(location: AppLocation): WorkspaceChromePreset {
  if (location.kind === "projects") return "project-library";
  if (location.workspace === "agent") {
    return "project-dashboard";
  }
  if (location.workspace === "runs") return "task-workspace";
  return "standard-workspace";
}
