import type {
  ProjectCreateRequest,
  ProjectCreateResponse,
  ProjectRunArtifactsResponse,
  ProjectRunDetailResponse,
  ProjectRunEventsResponse,
  ProjectRunLogsResponse,
  RunArtifactPreviewResponse,
  RunLinkRecord,
  ProjectRunStateTimelineResponse,
} from "../../types";
import { requestJson } from "./client";

export async function createProjectFromDirectory(
  baseUrl: string,
  payload: ProjectCreateRequest,
): Promise<ProjectCreateResponse> {
  return requestJson<ProjectCreateResponse>("/api/projects/create", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function getProjectRun(baseUrl: string, projectId: string, runId: string) {
  return requestJson<ProjectRunDetailResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}`,
    { baseUrl },
  );
}

export async function getProjectRunArtifact(
  baseUrl: string,
  projectId: string,
  runId: string,
  artifactId: string,
) {
  return requestJson<RunArtifactPreviewResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { baseUrl },
  );
}

export async function getProjectRunStateTimeline(
  baseUrl: string,
  projectId: string,
  runId: string,
) {
  return requestJson<ProjectRunStateTimelineResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/state-timeline`,
    { baseUrl },
  );
}

export async function listProjectRunArtifacts(baseUrl: string, projectId: string, runId: string) {
  return requestJson<ProjectRunArtifactsResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/artifacts`,
    { baseUrl },
  );
}

export async function listProjectRunEvents(baseUrl: string, projectId: string, runId: string) {
  return requestJson<ProjectRunEventsResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/events`,
    { baseUrl },
  );
}

export async function listProjectRunLinks(
  baseUrl: string,
  projectId: string,
  reviewedPlanId?: string,
) {
  const query = reviewedPlanId ? `?reviewed_plan_id=${encodeURIComponent(reviewedPlanId)}` : "";
  return requestJson<{ ok: boolean; project_id: string; runs: RunLinkRecord[] }>(
    `/api/projects/${encodeURIComponent(projectId)}/runs${query}`,
    { baseUrl },
  );
}

export async function listProjectRunLogs(
  baseUrl: string,
  projectId: string,
  runId: string,
  options?: { maxBytes?: number; includeContent?: boolean },
) {
  const params = new URLSearchParams();
  if (options?.maxBytes !== undefined) {
    params.set("max_bytes", String(options.maxBytes));
  }
  if (options?.includeContent !== undefined) {
    params.set("include_content", String(options.includeContent));
  }
  const query = params.size ? `?${params.toString()}` : "";
  return requestJson<ProjectRunLogsResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/logs${query}`,
    { baseUrl },
  );
}

export async function listProjectRuns(baseUrl: string, projectId: string, reviewedPlanId?: string) {
  return listProjectRunLinks(baseUrl, projectId, reviewedPlanId);
}
