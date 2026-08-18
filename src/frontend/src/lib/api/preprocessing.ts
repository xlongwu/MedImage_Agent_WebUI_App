import type {
  BoldReferenceReadinessResponse,
  MotionQcReadinessResponse,
  NiftiQcSnapshotResponse,
  NiftiThumbnailResponse,
} from "../../types";
import { requestJson } from "./legacyCore";

export async function getLatestNativeFullPreprocessingRun(baseUrl: string, projectId: string) {
  return requestJson<import("../../types").NativeFullPreprocResponse>(
    baseUrl,
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/latest`,
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
