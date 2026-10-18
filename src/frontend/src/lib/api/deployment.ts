import { requestJson } from "./client";

export async function getDeploymentProfile(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/deployment/profile", { baseUrl });
}

export async function getDesktopConfig(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/desktop/config", { baseUrl });
}

export async function getDesktopHealth(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/desktop/health", { baseUrl });
}

export async function getHealth(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/health", { baseUrl });
}

export async function getProjectConfig(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/project-config", { baseUrl });
}

export async function getReleaseReadiness(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/release-readiness", { baseUrl });
}

export async function getReleaseReadinessV1(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/release/readiness", { baseUrl });
}

export async function saveDesktopConfig(baseUrl: string, payload: Record<string, unknown>) {
  return requestJson<Record<string, unknown>>("/api/desktop/config", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}
