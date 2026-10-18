import type {
  BidsValidationResponse,
  ConversionDryRunResponse,
  DataReadinessResponse,
} from "../../types";
import { requestJson } from "./client";

export async function getProjectBidsValidation(baseUrl: string, projectId: string) {
  return requestJson<BidsValidationResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/bids-validation`,
    { baseUrl },
  );
}

export async function getProjectDataReadiness(baseUrl: string, projectId: string) {
  return requestJson<DataReadinessResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/data-readiness`,
    { baseUrl },
  );
}

export async function getLatestConversionDryRun(baseUrl: string, projectId: string) {
  return requestJson<ConversionDryRunResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/conversion/dry-run/latest`,
    { baseUrl },
  );
}
