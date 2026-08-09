import type {
  AgentExecuteRequest,
  AgentPlanRequest,
  AgentRun,
  ExecuteReviewedResponse,
  ReviewedPlanRecord,
} from "../../types";
import { requestJson } from "./legacyCore";

export async function checkApprovalGate(
  baseUrl: string,
  payload: {
    plan: Record<string, unknown>;
    validation: Record<string, unknown>;
    approval: Record<string, unknown> | null;
  },
) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/approval/check", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// === Execute Reviewed ===

// === Audit Record ===

export async function createAgentPlan(baseUrl: string, payload: AgentPlanRequest) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/agent/plan", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function executeAgentPlan(baseUrl: string, payload: AgentExecuteRequest) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/agent/execute", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function executeReviewedDryRun(
  baseUrl: string,
  payload: {
    plan: Record<string, unknown>;
    approval: Record<string, unknown> | null;
    project_id?: string;
    reviewed_plan_id?: string;
    project_config_path?: string;
    persist_audit?: boolean;
    actor?: string;
  },
) {
  return requestJson<ExecuteReviewedResponse>(baseUrl, "/api/plans/execute-reviewed", {
    method: "POST",
    body: JSON.stringify({ ...payload, dry_run: true }),
  });
}

export async function executeReviewedPlan(
  baseUrl: string,
  payload: {
    plan: Record<string, unknown>;
    approval: Record<string, unknown> | null;
    project_id?: string;
    reviewed_plan_id?: string;
    project_config_path: string;
    actor?: string;
  },
) {
  return requestJson<ExecuteReviewedResponse>(baseUrl, "/api/plans/execute-reviewed", {
    method: "POST",
    body: JSON.stringify({
      plan: payload.plan,
      approval: payload.approval,
      project_id: payload.project_id,
      reviewed_plan_id: payload.reviewed_plan_id,
      project_config_path: payload.project_config_path,
      dry_run: false,
      confirm_execution: true,
      persist_audit: true,
      write_pipeline_yaml: true,
      actor: payload.actor ?? "frontend-user",
    }),
  });
}

export async function fetchAuditRecord(baseUrl: string, auditId: string) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/audit/records/${encodeURIComponent(auditId)}`,
  );
}

// === Execute Reviewed ===

export async function fetchToolCatalog(baseUrl: string) {
  return requestJson<{ ok: boolean; count: number; items: Array<Record<string, unknown>> }>(
    baseUrl,
    "/api/tools/catalog",
  );
}

// === LLM Planner ===

export async function generatePlanFromGoal(
  baseUrl: string,
  payload: {
    goal: string;
    provider?: string;
    project_id?: string;
    project_config_path?: string;
    constraints?: Record<string, unknown>;
  },
) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/planner/plan-from-goal", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// === Plan Validator ===

export async function getAgentRun(baseUrl: string, agentRunId: string) {
  return requestJson<AgentRun>(baseUrl, `/api/agent-runs/${encodeURIComponent(agentRunId)}`);
}

export async function getPipeline(baseUrl: string, pipelineName: string) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/pipelines/${encodeURIComponent(pipelineName)}`,
  );
}

export async function getProjectReviewedPlan(
  baseUrl: string,
  projectId: string,
  reviewedPlanId: string,
) {
  return requestJson<{ ok: boolean; reviewed_plan: ReviewedPlanRecord }>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(reviewedPlanId)}`,
  );
}

export async function listPipelines(baseUrl: string) {
  return requestJson<{ ok: boolean; pipelines: string[] }>(baseUrl, "/api/pipelines");
}

export async function listProjectReviewedPlans(baseUrl: string, projectId: string) {
  return requestJson<{
    ok: boolean;
    project_id: string;
    reviewed_plans: ReviewedPlanRecord[];
  }>(baseUrl, `/api/projects/${encodeURIComponent(projectId)}/plans`);
}

export async function saveReviewedPlan(
  baseUrl: string,
  projectId: string,
  payload: {
    plan: Record<string, unknown>;
    project_config_path: string;
    validation?: Record<string, unknown>;
    goal?: string;
    provider?: string;
    status?: string;
    warnings?: string[];
    goal_contract_candidate?: Record<string, unknown>;
    reviewed_actor?: string;
    planner_invocation?: Record<string, unknown>;
    planner_evidence?: Record<string, unknown>;
  },
) {
  return requestJson<{ ok: boolean; reviewed_plan: ReviewedPlanRecord }>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/plans`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function validatePlan(baseUrl: string, plan: Record<string, unknown>) {
  return requestJson<Record<string, unknown>>(baseUrl, "/api/plans/validate", {
    method: "POST",
    body: JSON.stringify({ plan }),
  });
}

// === Approval Gate ===
