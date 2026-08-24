import { useCallback, useState } from "react";

import type { AppLocation, ProjectWorkspace } from "./workspaceModel";
import { locationForProject } from "./workspaceModel";

export function useWorkspaceNavigation() {
  const [location, setLocation] = useState<AppLocation>({ kind: "projects" });

  const openProjects = useCallback(() => setLocation({ kind: "projects" }), []);
  const openProject = useCallback(
    (projectId: string) => setLocation(locationForProject(projectId)),
    [],
  );
  const openWorkspace = useCallback((projectId: string, workspace: ProjectWorkspace) => {
    setLocation({ kind: "project", projectId, workspace });
  }, []);
  return {
    location,
    openProject,
    openProjects,
    openWorkspace,
    setLocation,
  };
}
