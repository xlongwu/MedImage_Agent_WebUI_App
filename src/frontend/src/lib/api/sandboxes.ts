import type { SandboxAttempt, SandboxAttemptsResponse } from "../types/sandbox";
import { requestJson } from "./legacyCore";

export async function listSandboxAttempts(
  baseUrl: string,
  projectId: string,
  runId: string,
): Promise<SandboxAttemptsResponse> {
  return requestJson<SandboxAttemptsResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/sandbox-attempts`,
  );
}

export async function getSandboxAttempt(
  baseUrl: string,
  projectId: string,
  runId: string,
  sandboxId: string,
): Promise<{ ok: boolean; project_id: string; run_id: string; sandbox_attempt: SandboxAttempt }> {
  return requestJson(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/sandbox-attempts/${encodeURIComponent(sandboxId)}`,
  );
}
