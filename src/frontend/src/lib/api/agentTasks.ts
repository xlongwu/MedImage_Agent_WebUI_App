import type {
  AgentTaskEventPage,
  AgentHarnessActivityPage,
  AgentTaskListResponse,
  AgentTaskResponse,
  AnswerAgentTaskRequest,
  ApproveAgentTaskRequest,
  ApproveAgentTaskRecoveryRequest,
  CancelAgentTaskRequest,
  CreateAgentTaskRequest,
} from "../types/agentTask";
import { getJson, postJson } from "./client";

type RequestControls = {
  signal?: AbortSignal;
};

type EventRequestControls = RequestControls & {
  after?: string | null;
  limit?: number;
};

function taskCollectionPath(projectId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/agent/tasks`;
}

function taskPath(projectId: string, taskId: string): string {
  return `${taskCollectionPath(projectId)}/${encodeURIComponent(taskId)}`;
}

export function listAgentTasks(
  baseUrl: string,
  projectId: string,
  controls: RequestControls = {},
): Promise<AgentTaskListResponse> {
  return getJson<AgentTaskListResponse>(taskCollectionPath(projectId), {
    baseUrl,
    signal: controls.signal,
  });
}

export function getAgentTask(
  baseUrl: string,
  projectId: string,
  taskId: string,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return getJson<AgentTaskResponse>(taskPath(projectId, taskId), {
    baseUrl,
    signal: controls.signal,
  });
}

export function listAgentTaskEvents(
  baseUrl: string,
  projectId: string,
  taskId: string,
  controls: EventRequestControls = {},
): Promise<AgentTaskEventPage> {
  const params = new URLSearchParams();
  if (controls.after) params.set("after", controls.after);
  if (controls.limit !== undefined) params.set("limit", String(controls.limit));
  const query = params.toString();
  return getJson<AgentTaskEventPage>(
    `${taskPath(projectId, taskId)}/events${query ? `?${query}` : ""}`,
    {
      baseUrl,
      signal: controls.signal,
    },
  );
}

export function getAgentTaskHarness(
  baseUrl: string,
  projectId: string,
  taskId: string,
  controls: RequestControls = {},
): Promise<AgentHarnessActivityPage> {
  return getJson<AgentHarnessActivityPage>(`${taskPath(projectId, taskId)}/harness`, {
    baseUrl,
    signal: controls.signal,
  });
}

export function createAgentTask(
  baseUrl: string,
  projectId: string,
  request: CreateAgentTaskRequest,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return postJson<AgentTaskResponse>(taskCollectionPath(projectId), request, {
    baseUrl,
    signal: controls.signal,
  });
}

export function answerAgentTask(
  baseUrl: string,
  projectId: string,
  taskId: string,
  request: AnswerAgentTaskRequest,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return postJson<AgentTaskResponse>(`${taskPath(projectId, taskId)}/answer`, request, {
    baseUrl,
    signal: controls.signal,
  });
}

export function approveAgentTask(
  baseUrl: string,
  projectId: string,
  taskId: string,
  request: ApproveAgentTaskRequest,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return getAgentApprovalToken().then((approvalToken) =>
    postJson<AgentTaskResponse>(`${taskPath(projectId, taskId)}/approve`, request, {
      baseUrl,
      headers: approvalToken ? { "X-MedImage-Agent-Approval-Token": approvalToken } : undefined,
      signal: controls.signal,
    }),
  );
}

async function getAgentApprovalToken(): Promise<string | null> {
  if (!window.medimage?.getAgentApprovalToken) {
    return null;
  }
  return window.medimage.getAgentApprovalToken();
}

export function cancelAgentTask(
  baseUrl: string,
  projectId: string,
  taskId: string,
  request: CancelAgentTaskRequest,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return postJson<AgentTaskResponse>(`${taskPath(projectId, taskId)}/cancel`, request, {
    baseUrl,
    signal: controls.signal,
  });
}

export function approveAgentTaskRecovery(
  baseUrl: string,
  projectId: string,
  taskId: string,
  request: ApproveAgentTaskRecoveryRequest,
  controls: RequestControls = {},
): Promise<AgentTaskResponse> {
  return getAgentApprovalToken().then((approvalToken) =>
    postJson<AgentTaskResponse>(`${taskPath(projectId, taskId)}/approve-recovery`, request, {
      baseUrl,
      headers: approvalToken ? { "X-MedImage-Agent-Approval-Token": approvalToken } : undefined,
      signal: controls.signal,
    }),
  );
}
