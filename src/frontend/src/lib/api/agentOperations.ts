import { getJson } from "./client";
import type { AgentOperationalSummary } from "../types/agentOperations";

export function getAgentOperationalSummary(
  baseUrl: string,
  projectId: string,
): Promise<AgentOperationalSummary> {
  return getJson<AgentOperationalSummary>(
    `/api/projects/${encodeURIComponent(projectId)}/agent-operations/summary?window_hours=168`,
    { baseUrl },
  );
}
