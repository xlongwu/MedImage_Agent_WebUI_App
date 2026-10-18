import { requestJson } from "./client";

export async function createSchedulerPlan(
  baseUrl: string,
  payload: {
    project_config_path: string;
    pipeline_path: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/scheduler/plan", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}
