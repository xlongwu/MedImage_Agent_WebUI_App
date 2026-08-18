import type { QcDashboardReportResponse, DatasetEvaluationReport } from "../../types";
import { requestJson } from "./legacyCore";

export async function getDatasetEvaluationReport(baseUrl: string) {
  return requestJson<DatasetEvaluationReport>(baseUrl, "/api/reports/dataset-evaluation");
}

export async function getImageManifestReport(baseUrl: string, projectId: string) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/images/manifest?project_id=${encodeURIComponent(projectId)}`,
  );
}

export async function getImageValidationReport(baseUrl: string, projectId: string) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/images/validation?project_id=${encodeURIComponent(projectId)}`,
  );
}

export async function getLatestQcDashboardReport(baseUrl: string, projectId: string) {
  return requestJson<QcDashboardReportResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/qc-dashboard/report/latest`,
  );
}
