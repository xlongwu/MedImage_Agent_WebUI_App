import { requestJson } from "./client";

// Deprecated compatibility client for the old SessionDB run-history surface.
// This is not the project-scoped long-term memory API; new code uses
// project-history APIs for runs and ./memory for reviewed project memory.

export async function getSessionRuns(baseUrl: string, status?: string) {
  const params = status ? `?status=${encodeURIComponent(status)}` : "";
  return requestJson<Record<string, unknown>>(`/api/sessions/runs${params}`, { baseUrl });
}

export async function postSessionIndex(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/sessions/index", { method: "POST", baseUrl });
}

export async function querySessions(baseUrl: string, q: string) {
  return requestJson<Record<string, unknown>>(
    `/api/sessions/query?q=${encodeURIComponent(q)}&limit=50`,
    { baseUrl },
  );
}

// === Tool Catalog ===
