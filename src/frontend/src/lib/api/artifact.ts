import { requestJson } from "./client";

export async function createBundle(
  baseUrl: string,
  payload: {
    bundle_id?: string;
    include_logs?: boolean;
    include_reports?: boolean;
    include_artifact_index?: boolean;
    max_file_size_bytes?: number;
  },
) {
  return requestJson<Record<string, unknown>>("/api/bundles/create", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
  });
}

export async function getArtifacts(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/artifacts", { baseUrl });
}

export async function inspectBundle(baseUrl: string, bundleId: string) {
  return requestJson<Record<string, unknown>>(`/api/bundles/${encodeURIComponent(bundleId)}`, {
    baseUrl,
  });
}

export async function listBundles(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/bundles", { baseUrl });
}

export async function previewArtifact(baseUrl: string, path: string) {
  return requestJson<Record<string, unknown>>("/api/artifacts/preview", {
    method: "POST",
    body: JSON.stringify({ path }),
    baseUrl,
  });
}

export async function refreshArtifacts(baseUrl: string) {
  return requestJson<Record<string, unknown>>("/api/artifacts/refresh", {
    method: "POST",
    baseUrl,
  });
}
