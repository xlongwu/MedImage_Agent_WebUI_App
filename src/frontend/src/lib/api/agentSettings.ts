import { getJson, requestJson } from "./client";

export type RegisteredScientificResource = {
  name: string;
  path: string;
  license: string;
  checksum: string;
};

export type ProjectAgentSettings = {
  schema_version: 1;
  project_id: string;
  default_atlas: RegisteredScientificResource | null;
  default_template: RegisteredScientificResource | null;
  cpu_policy: "auto" | "serial" | "process";
  compute_policy: "auto" | "cpu" | "gpu";
};

export type ProjectAgentSettingsUpdate = {
  default_atlas: Omit<RegisteredScientificResource, "checksum"> | null;
  default_template: Omit<RegisteredScientificResource, "checksum"> | null;
  cpu_policy: ProjectAgentSettings["cpu_policy"];
  compute_policy: ProjectAgentSettings["compute_policy"];
};

const endpoint = (projectId: string) =>
  `/api/projects/${encodeURIComponent(projectId)}/agent-settings`;

export function getProjectAgentSettings(baseUrl: string, projectId: string) {
  return getJson<ProjectAgentSettings>(endpoint(projectId), { baseUrl });
}

export function updateProjectAgentSettings(
  baseUrl: string,
  projectId: string,
  payload: ProjectAgentSettingsUpdate,
) {
  return requestJson<ProjectAgentSettings>(endpoint(projectId), {
    baseUrl,
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
