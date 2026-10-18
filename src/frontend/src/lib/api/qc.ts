import type { QcDashboardReportResponse, DatasetEvaluationReport } from "../../types";
import { requestJson } from "./client";

export async function getDatasetEvaluationReport(baseUrl: string) {
  return requestJson<DatasetEvaluationReport>("/api/reports/dataset-evaluation", { baseUrl });
}

export async function getImageManifestReport(baseUrl: string, projectId: string) {
  return requestJson<Record<string, unknown>>(
    `/api/images/manifest?project_id=${encodeURIComponent(projectId)}`,
    { baseUrl },
  );
}

export async function getImageValidationReport(baseUrl: string, projectId: string) {
  return requestJson<Record<string, unknown>>(
    `/api/images/validation?project_id=${encodeURIComponent(projectId)}`,
    { baseUrl },
  );
}

export async function getLatestQcDashboardReport(baseUrl: string, projectId: string) {
  return requestJson<QcDashboardReportResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/qc-dashboard/report/latest`,
    { baseUrl },
  );
}
