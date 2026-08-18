import { requestJson } from "./legacyCore";

export async function getDeploymentProfile(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/deployment/profile");
}

export async function getDesktopConfig(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/desktop/config");
}

export async function getDesktopHealth(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/desktop/health");
}

export async function getHealth(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/health");
}

export async function getProjectConfig(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/project-config");
}

export async function getReleaseReadiness(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/release-readiness");
}

export async function getReleaseReadinessV1(baseUrl: string) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/release/readiness");
}

export async function saveDesktopConfig(baseUrl: string, payload: Record<string, unknown>) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/desktop/config", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
