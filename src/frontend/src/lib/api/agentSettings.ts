import { getJson, requestJson } from "./client";

export type RegisteredScientificResource = {
  name: string;
  path: string;
  license: string;
  checksum: string;
  space: "MNI152" | "native";
};

export type AgentTeamConsentDataCategory =
  | "goal_and_contract"
  | "deidentified_science_evidence"
  | "capability_and_tool_summary"
  | "policy_and_scope_summary"
  | "decision_and_plan_coverage"
  | "environment_summary"
  | "budget_summary";

export type ProjectAgentSettings = {
  schema_version: 1;
  project_id: string;
  default_atlas: RegisteredScientificResource | null;
  default_template: RegisteredScientificResource | null;
  cpu_policy: "auto" | "serial" | "process";
  compute_policy: "auto" | "cpu" | "gpu";
  multi_agent_consent: {
    schema_version: 1;
    project_id: string;
    enabled: boolean;
    consent_epoch: number;
    consent_hash: string;
    allowed_data_categories: AgentTeamConsentDataCategory[];
    max_model_calls: number;
    max_input_tokens: number;
    updated_at: string | null;
  };
};

export type ProjectAgentSettingsUpdate = {
  default_atlas: Omit<RegisteredScientificResource, "checksum"> | null;
  default_template: Omit<RegisteredScientificResource, "checksum"> | null;
  cpu_policy: ProjectAgentSettings["cpu_policy"];
  compute_policy: ProjectAgentSettings["compute_policy"];
  multi_agent_consent_enabled: boolean;
  multi_agent_max_model_calls: number;
  multi_agent_max_input_tokens: number;
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
