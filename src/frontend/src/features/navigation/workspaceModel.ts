export type ProjectWorkspace = "agent" | "runs" | "settings";

export type AppLocation =
  | { kind: "projects" }
  | { kind: "project"; projectId: string; workspace: ProjectWorkspace };

export const projectWorkspaces: ProjectWorkspace[] = ["agent", "runs", "settings"];

export function locationForProject(projectId: string): AppLocation {
  return { kind: "project", projectId, workspace: "agent" };
}
