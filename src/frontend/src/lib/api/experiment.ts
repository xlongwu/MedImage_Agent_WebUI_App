import { requestJson } from "./client";

export async function compareExperimentRuns(
  baseUrl: string,
  payload: {
    experiment_id: string;
    run_ids: string[];
  },
) {
  return requestJson<Record<string, unknown>>("/api/experiments/compare", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function createExperimentRecord(
  baseUrl: string,
  payload: {
    experiment_id: string;
    name: string;
    run_ids: string[];
    tags: string[];
    notes: string;
  },
) {
  return requestJson<Record<string, unknown>>("/api/experiments/record", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function getExperimentComparison(baseUrl: string, experimentId: string) {
  return requestJson<Record<string, unknown>>(`/api/experiments/comparison/${experimentId}`, {
    baseUrl,
  });
}

export async function getExperimentDashboard(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/experiments/dashboard", { baseUrl });
}

export async function getExperimentRecord(baseUrl: string, experimentId: string) {
  return requestJson<Record<string, unknown>>(`/api/experiments/record/${experimentId}`, {
    baseUrl,
  });
}

export async function getExperimentsRunIndex(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/experiments/run-index", { baseUrl });
}

export async function refreshExperimentDashboard(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/experiments/dashboard/refresh", {
    method: "POST",
    baseUrl,
  });
}
