import type {
  BidsValidationResponse,
  ConversionDryRunResponse,
  DataReadinessResponse,
} from "../../types";
import { requestJson } from "./legacyCore";

export async function getProjectBidsValidation(baseUrl: string, projectId: string) {
  return requestJson<BidsValidationResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/bids-validation`,
  );
}

export async function getProjectDataReadiness(baseUrl: string, projectId: string) {
  return requestJson<DataReadinessResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/data-readiness`,
  );
}

export async function getLatestConversionDryRun(baseUrl: string, projectId: string) {
  return requestJson<ConversionDryRunResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/conversion/dry-run/latest`,
  );
}
