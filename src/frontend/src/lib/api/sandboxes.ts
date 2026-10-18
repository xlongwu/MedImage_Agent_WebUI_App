import type { SandboxAttempt, SandboxAttemptsResponse } from "../types/sandbox";
import { requestJson } from "./client";

export async function listSandboxAttempts(
  baseUrl: string,
  projectId: string,
  runId: string,
): Promise<SandboxAttemptsResponse> {
  return requestJson<SandboxAttemptsResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/sandbox-attempts`,
    { baseUrl },
  );
}

export async function getSandboxAttempt(
  baseUrl: string,
  projectId: string,
  runId: string,
  sandboxId: string,
): Promise<{ ok: boolean; project_id: string; run_id: string; sandbox_attempt: SandboxAttempt }> {
  return requestJson(
    `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(runId)}/sandbox-attempts/${encodeURIComponent(sandboxId)}`,
    { baseUrl },
  );
}
