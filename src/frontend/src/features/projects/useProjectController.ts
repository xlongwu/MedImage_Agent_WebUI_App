import { useCallback, useState } from "react";

import type { ProjectCreateResponse } from "../../types";
import type { ProjectSummary } from "../../lib/types/project";
import {
  isProjectNameConflict,
  mergeCreatedProjectIntoList,
  uniqueProjectName,
} from "../../lib/projectWorkflow";
import { createProjectFromDirectory, deleteProject, getApiBaseUrl } from "../../lib/api";
import { useProjects } from "../../hooks/useProjects";

export interface ProjectController {
  projects: { data: ProjectSummary[]; loading: boolean; error: string };
  projectsLoading: boolean;
  projectsError: string;
  reloadProjects: () => Promise<ProjectSummary[] | void>;
  projectCreateLoading: boolean;
  projectCreateError: string;
  projectCreateResult: ProjectCreateResponse | null;
  deletingProjectId: string | null;
  projectDeleteError: string;
  setProjectDeleteError: (error: string) => void;
  setProjectCreateResult: (result: ProjectCreateResponse | null) => void;
  setProjectCreateError: (error: string) => void;
  selectProjectDirectory: () => Promise<string | null>;
  createProjectFromDirectoryPath: (
    path: string,
    options?: { projectName?: string },
  ) => Promise<ProjectCreateResponse | null>;
  handleDeleteProject: (projectId: string, projectName: string) => Promise<void>;
}

export function useProjectController(
  selectedProjectId: string | null = null,
  setSelectedProjectId: ((id: string | null) => void) | undefined = undefined,
): ProjectController {
  const projects = useProjects();
  const [projectCreateLoading, setProjectCreateLoading] = useState(false);
  const [projectCreateError, setProjectCreateError] = useState("");
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null);
  const [projectDeleteError, setProjectDeleteError] = useState("");
  const [projectCreateResult, setProjectCreateResult] = useState<ProjectCreateResponse | null>(
    null,
  );

  const selectProjectDirectory = useCallback(async () => {
    setProjectCreateError("");
    try {
      if (window.medimage?.selectDirectory) {
        const selectedPath = await window.medimage.selectDirectory();
        return selectedPath?.trim() ? selectedPath.trim() : null;
      }
      const selectedPath = window.prompt("Enter a local BIDS / rawdata directory path");
      return selectedPath?.trim() ? selectedPath.trim() : null;
    } catch (error) {
      setProjectCreateError(error instanceof Error ? error.message : String(error));
      return null;
    }
  }, []);

  const createProjectFromDirectoryPath = useCallback(
    async (path: string, options?: { projectName?: string }) => {
      const selectedPath = path.trim();
      if (!selectedPath) return null;
      setProjectCreateError("");
      setProjectCreateResult(null);
      setProjectCreateLoading(true);
      try {
        const baseUrl = await getApiBaseUrl();
        const requestedName = uniqueProjectName(
          options?.projectName?.trim() || directoryBasename(selectedPath),
          projects.data,
        );
        let effectiveName = requestedName;
        const createWithName = (projectName: string) =>
          createProjectFromDirectory(baseUrl, {
            project_name: projectName,
            rawdata_dir: selectedPath,
            copy_mode: "reference",
            run_inspection: true,
            overwrite: false,
          });

        let result: ProjectCreateResponse;
        try {
          result = await createWithName(effectiveName);
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          if (!isProjectNameConflict(message)) throw error;
          effectiveName = uniqueProjectName(
            `${requestedName} ${new Date().toISOString().slice(0, 10)}`,
            projects.data,
          );
          result = await createWithName(effectiveName);
        }

        const refreshed = await projects.reload();
        projects.setData(mergeCreatedProjectIntoList(result, refreshed ?? projects.data));
        setSelectedProjectId?.(result.project_id);
        setProjectCreateResult(result);
        return result;
      } catch (error) {
        setProjectCreateError(error instanceof Error ? error.message : String(error));
        return null;
      } finally {
        setProjectCreateLoading(false);
      }
    },
    [projects, setSelectedProjectId],
  );

  const handleDeleteProject = useCallback(
    async (projectId: string) => {
      if (projectCreateLoading || deletingProjectId) return;
      setProjectDeleteError("");
      setDeletingProjectId(projectId);
      try {
        await deleteProject(projectId);
        const remaining = projects.data.filter((item) => item.id !== projectId);
        projects.setData(remaining);
        if (selectedProjectId === projectId) {
          setSelectedProjectId?.(remaining[0]?.id ?? null);
          setProjectCreateResult(null);
        }
        const refreshed = await projects.reload();
        const latest = (refreshed ?? remaining).filter((item) => item.id !== projectId);
        projects.setData(latest);
        if (selectedProjectId === projectId) setSelectedProjectId?.(latest[0]?.id ?? null);
      } catch (error) {
        setProjectDeleteError(error instanceof Error ? error.message : String(error));
      } finally {
        setDeletingProjectId(null);
      }
    },
    [deletingProjectId, projectCreateLoading, projects, selectedProjectId, setSelectedProjectId],
  );

  return {
    projects: { data: projects.data, loading: projects.loading, error: projects.error },
    projectsLoading: projects.loading,
    projectsError: projects.error,
    reloadProjects: projects.reload,
    projectCreateLoading,
    projectCreateError,
    projectCreateResult,
    deletingProjectId,
    projectDeleteError,
    setProjectDeleteError,
    setProjectCreateResult,
    setProjectCreateError,
    selectProjectDirectory,
    createProjectFromDirectoryPath,
    handleDeleteProject,
  };
}

function directoryBasename(path: string): string {
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? path;
}
