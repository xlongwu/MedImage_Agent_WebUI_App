import { deleteJson, getJson, postJson } from "./client";
import type { ProjectCreateRequest, ProjectCreateResponse } from "../../types";
import type { ProjectDetail, ProjectSummary, StudyOverview } from "../types/project";

export interface ProjectDeleteResponse {
  ok: boolean;
  project_id: string;
  removed_from_store: boolean;
  removed_from_recent: boolean;
  deleted_files: false;
  message: string;
  warning?: string;
}

export function getProjects(): Promise<ProjectSummary[]> {
  return getJson<ProjectSummary[]>("/api/agent/projects");
}

export function getProject(projectId: string): Promise<ProjectDetail> {
  return getJson<ProjectDetail>(`/api/projects/${encodeURIComponent(projectId)}`);
}

export function getStudyOverview(studyId: string): Promise<StudyOverview> {
  return getJson<StudyOverview>(`/api/studies/${encodeURIComponent(studyId)}/overview`);
}

export function deleteProject(projectId: string): Promise<ProjectDeleteResponse> {
  return deleteJson<ProjectDeleteResponse>(`/api/projects/${encodeURIComponent(projectId)}`);
}

export function createProjectFromDirectory(
  _baseUrl: string,
  payload: ProjectCreateRequest,
): Promise<ProjectCreateResponse> {
  return postJson<ProjectCreateResponse>("/api/projects/create", payload);
}
