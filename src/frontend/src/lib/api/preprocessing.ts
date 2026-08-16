import type {
  BoldReferenceReadinessResponse,
  MotionMetricsDraftResponse,
  MotionQcReadinessResponse,
  SpmRealignDryRunResponse,
  SpmRealignWrapperSkeletonResponse,
  NiftiQcSnapshotResponse,
  NiftiThumbnailResponse,
} from "../../types";
import { requestJson } from "./legacyCore";

export async function createPreprocessingRun(
  baseUrl: string,
  projectId: string,
  body: {
    preprocessing_input_dir?: string;
    confirm_use_converted_input?: boolean;
    confirm_no_rawdata_modification?: boolean;
    confirm_python_only_execution?: boolean;
    confirm_no_spm_matlab?: boolean;
  },
) {
  return requestJson<import("../../types").PreprocessingRunCreateResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function executePreprocessingPythonPreflight(
  baseUrl: string,
  projectId: string,
  preprocessingRunId: string,
) {
  return requestJson<import("../../types").PreprocessingRunExecuteResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs/${encodeURIComponent(preprocessingRunId)}/execute-python-preflight`,
    { method: "POST" },
  );
}

export async function runNativeFullPreprocessingDryRun(
  baseUrl: string,
  projectId: string,
  body: import("../../types").NativeFullPreprocRequest,
) {
  return requestJson<import("../../types").NativeFullPreprocResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/full/dry-run`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function getNativeGpuDetection(baseUrl: string) {
  return requestJson<import("../../types").NativeGpuDetection>(baseUrl, "/api/gpu/detect");
}

export async function getNativeFullPreprocessingProgress(
  baseUrl: string,
  projectId: string,
  runId: string,
) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/${encodeURIComponent(runId)}/progress`,
  );
}

export async function getNativeFullPreprocessingRun(
  baseUrl: string,
  projectId: string,
  runId: string,
) {
  return requestJson<import("../../types").NativeFullPreprocResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/${encodeURIComponent(runId)}`,
  );
}

export async function getLatestNativeFullPreprocessingRun(baseUrl: string, projectId: string) {
  return requestJson<import("../../types").NativeFullPreprocResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/latest`,
  );
}

export async function getNativeFullPreprocessingValidation(
  baseUrl: string,
  projectId: string,
  runId: string,
) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/${encodeURIComponent(runId)}/validation`,
  );
}

export async function getNativeFullPreprocessingReport(
  baseUrl: string,
  projectId: string,
  runId: string,
) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/${encodeURIComponent(runId)}/report`,
  );
}

export async function generateMotionMetricsDraft(baseUrl: string, projectId: string) {
  return requestJson<MotionMetricsDraftResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/motion-qc/metrics-draft`,
    { method: "POST" },
  );
}

export async function generateSpmRealignWrapperSkeleton(baseUrl: string, projectId: string) {
  return requestJson<SpmRealignWrapperSkeletonResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/spm-realign/wrapper-skeleton`,
    { method: "POST" },
  );
}

export async function getPreprocessingPipelineReport(
  baseUrl: string,
  projectId: string,
  preprocessingRunId: string,
) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs/${encodeURIComponent(preprocessingRunId)}/report`,
  );
}

export async function getPreprocessingPipelineValidation(
  baseUrl: string,
  projectId: string,
  preprocessingRunId: string,
) {
  return requestJson<Record<string, unknown>>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs/${encodeURIComponent(preprocessingRunId)}/validation`,
  );
}

export async function getPreprocessingPlanPreview(baseUrl: string, projectId: string) {
  return requestJson<import("../../types").PreprocessingPlanPreviewResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/plan/preview`,
    { method: "POST" },
  );
}

export async function getPreprocessingRunStatus(
  baseUrl: string,
  projectId: string,
  preprocessingRunId: string,
) {
  return requestJson<import("../../types").PreprocessingRunStatusResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs/${encodeURIComponent(preprocessingRunId)}`,
  );
}

export async function getProjectBoldReferenceReadiness(baseUrl: string, projectId: string) {
  return requestJson<BoldReferenceReadinessResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/bold-reference/readiness`,
  );
}

export async function getProjectMotionQcReadiness(baseUrl: string, projectId: string) {
  return requestJson<MotionQcReadinessResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/motion-qc/readiness`,
  );
}

export async function getProjectNiftiQcSnapshot(baseUrl: string, projectId: string) {
  return requestJson<NiftiQcSnapshotResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/nifti-qc/snapshot`,
  );
}

export async function getProjectNiftiThumbnail(
  baseUrl: string,
  projectId: string,
  imageId: string,
  options?: {
    view?: "axial" | "coronal" | "sagittal" | "all";
    volumeIndex?: number;
    size?: number;
  },
) {
  const params = new URLSearchParams();
  if (options?.view) params.set("view", options.view);
  if (options?.volumeIndex !== undefined) params.set("volume_index", String(options.volumeIndex));
  if (options?.size !== undefined) params.set("size", String(options.size));
  const qs = params.toString();
  return requestJson<NiftiThumbnailResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/nifti-qc/images/${encodeURIComponent(imageId)}/thumbnail${qs ? "?" + qs : ""}`,
  );
}

export async function registerConvertedPreprocessingInput(
  baseUrl: string,
  projectId: string,
  body: {
    conversion_run_id: string;
    converted_bids_dir?: string;
    mode?: string;
    confirm_rawdata_readonly?: boolean;
    confirm_use_converted_outputs?: boolean;
  },
) {
  return requestJson<import("../../types").PreprocessingInputRegistrationResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/input/register-converted`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function runFilteringDryRun(
  baseUrl: string,
  projectId: string,
  preprocessingRunId: string,
  body: {
    functional_input_dir?: string;
    low_cut_hz?: number;
    high_cut_hz?: number;
    confirm_dry_run_only?: boolean;
  },
) {
  return requestJson<import("../../types").FilteringDryRunResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/runs/${encodeURIComponent(preprocessingRunId)}/temporal-filtering/dry-run`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function runSpmRealignDryRun(baseUrl: string, projectId: string) {
  return requestJson<SpmRealignDryRunResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/spm-realign/dry-run`,
    { method: "POST" },
  );
}
