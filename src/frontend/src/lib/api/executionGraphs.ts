import type { ExecutionGraphResponse } from "../types/executionGraph";
import { requestJson } from "./client";

export function previewExecutionGraph(
  baseUrl: string,
  projectId: string,
  plan: Record<string, unknown>,
  signal?: AbortSignal,
) {
  return requestJson<ExecutionGraphResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/plan-graph-preview`,
    { method: "POST", body: JSON.stringify({ plan }), signal, baseUrl },
  );
}

export function getPlanExecutionGraph(
  baseUrl: string,
  projectId: string,
  reviewedPlanId: string,
  signal?: AbortSignal,
) {
  return requestJson<ExecutionGraphResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(reviewedPlanId)}/graph`,
    { signal, baseUrl },
  );
}

export function getRunExecutionGraph(
  baseUrl: string,
  projectId: string,
  runId: string,
  signal?: AbortSignal,
) {
  return requestJson<ExecutionGraphResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/graph`,
    { signal, baseUrl },
  );
}
