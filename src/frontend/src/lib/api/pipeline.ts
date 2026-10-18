import type { ExecuteReviewedResponse, ReviewedPlanRecord } from "../../types";
import { requestJson } from "./client";

export async function checkApprovalGate(
  baseUrl: string,
  payload: {
    plan: Record<string, unknown>;
    validation: Record<string, unknown>;
    approval: Record<string, unknown> | null;
  },
) {
  return requestJson<Record<string, unknown>>("/api/approval/check", {
    method: "POST",
    body: JSON.stringify(payload),
    baseUrl,
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
  return requestJson<ExecuteReviewedResponse>("/api/plans/execute-reviewed", {
    method: "POST",
    body: JSON.stringify({ ...payload, dry_run: true }),
    baseUrl,
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
  return requestJson<ExecuteReviewedResponse>("/api/plans/execute-reviewed", {
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
    baseUrl,
  });
}

export async function fetchAuditRecord(baseUrl: string, auditId: string) {
  return requestJson<Record<string, unknown>>(`/api/audit/records/${encodeURIComponent(auditId)}`, {
    baseUrl,
  });
}

// === Execute Reviewed ===

export async function fetchToolCatalog(baseUrl: string) {
  return requestJson<{ ok: boolean; count: number; items: Array<Record<string, unknown>> }>(
    "/api/tools/catalog",
    { baseUrl },
  );
}

export async function getPipeline(baseUrl: string, pipelineName: string) {
  return requestJson<Record<string, unknown>>(
    `/api/pipelines/${encodeURIComponent(pipelineName)}`,
    { baseUrl },
  );
}

export async function getProjectReviewedPlan(
  baseUrl: string,
  projectId: string,
  reviewedPlanId: string,
) {
  return requestJson<{ ok: boolean; reviewed_plan: ReviewedPlanRecord }>(
    `/api/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(reviewedPlanId)}`,
    { baseUrl },
  );
}

export async function listPipelines(baseUrl: string) {
  return requestJson<{ ok: boolean; pipelines: string[] }>("/api/pipelines", { baseUrl });
}

export async function listProjectReviewedPlans(baseUrl: string, projectId: string) {
  return requestJson<{
    ok: boolean;
    project_id: string;
    reviewed_plans: ReviewedPlanRecord[];
  }>(`/api/projects/${encodeURIComponent(projectId)}/plans`, { baseUrl });
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
    `/api/projects/${encodeURIComponent(projectId)}/plans`,
    { method: "POST", body: JSON.stringify(payload), baseUrl },
  );
}

export async function validatePlan(baseUrl: string, plan: Record<string, unknown>) {
  return requestJson<Record<string, unknown>>("/api/plans/validate", {
    method: "POST",
    body: JSON.stringify({ plan }),
    baseUrl,
  });
}

// === Approval Gate ===
