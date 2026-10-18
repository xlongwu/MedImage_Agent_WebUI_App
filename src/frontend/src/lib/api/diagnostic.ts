import type { RunInspection } from "../../types";
import { requestJson } from "./client";

export async function diagnoseRun(baseUrl: string, runId: string) {
  return requestJson<Record<string, unknown>>(`/api/runs/${encodeURIComponent(runId)}/diagnosis`, {
    baseUrl,
  });
}

export async function getDatasetImportHistory(baseUrl: string, projectId: string) {
  return requestJson<Record<string, unknown>>(
    `/api/datasets/imports?project_id=${encodeURIComponent(projectId)}`,
    { baseUrl },
  );
}

export async function getRetryRun(baseUrl: string, retryRunId: string) {
  return requestJson<Record<string, unknown>>(`/api/retry-runs/${encodeURIComponent(retryRunId)}`, {
    baseUrl,
  });
}

export async function inspectRun(baseUrl: string, runId: string) {
  return requestJson<RunInspection>(`/api/runs/${encodeURIComponent(runId)}`, { baseUrl });
}

export async function listRuns(baseUrl: string) {
  return requestJson<{ ok: boolean; runs: Array<Record<string, unknown>> }>("/api/runs", {
    baseUrl,
  });
}

export async function readLog(baseUrl: string, path: string) {
  return requestJson<{
    ok: boolean;
    path: string;
    relative_path: string;
    content: string;
    size_bytes: number;
  }>(`/api/logs/read?path=${encodeURIComponent(path)}`, { baseUrl });
}

export async function retryDryRun(baseUrl: string, runId: string, retryRunId?: string) {
  return requestJson<Record<string, unknown>>("/api/retry/dry-run", {
    method: "POST",
    body: JSON.stringify({ run_id: runId, retry_run_id: retryRunId }),
    baseUrl,
  });
}

export async function retryExecute(
  baseUrl: string,
  runId: string,
  projectConfigPath: string,
  retryRunId?: string,
  approved = false,
) {
  return requestJson<Record<string, unknown>>("/api/retry/execute", {
    method: "POST",
    body: JSON.stringify({
      run_id: runId,
      project_config_path: projectConfigPath,
      retry_run_id: retryRunId,
      approved,
    }),
    baseUrl,
  });
}
