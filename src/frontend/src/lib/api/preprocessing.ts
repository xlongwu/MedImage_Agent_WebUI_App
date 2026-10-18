import type {
  BoldReferenceReadinessResponse,
  MotionQcReadinessResponse,
  NiftiQcSnapshotResponse,
  NiftiThumbnailResponse,
} from "../../types";
import { requestJson } from "./client";

export async function getLatestNativeFullPreprocessingRun(baseUrl: string, projectId: string) {
  return requestJson<import("../../types").NativeFullPreprocResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/preprocessing/native/runs/latest`,
    { baseUrl },
  );
}

export async function getProjectBoldReferenceReadiness(baseUrl: string, projectId: string) {
  return requestJson<BoldReferenceReadinessResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/bold-reference/readiness`,
    { baseUrl },
  );
}

export async function getProjectMotionQcReadiness(baseUrl: string, projectId: string) {
  return requestJson<MotionQcReadinessResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/motion-qc/readiness`,
    { baseUrl },
  );
}

export async function getProjectNiftiQcSnapshot(baseUrl: string, projectId: string) {
  return requestJson<NiftiQcSnapshotResponse>(
    `/api/projects/${encodeURIComponent(projectId)}/nifti-qc/snapshot`,
    { baseUrl },
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
    `/api/projects/${encodeURIComponent(projectId)}/nifti-qc/images/${encodeURIComponent(imageId)}/thumbnail${qs ? "?" + qs : ""}`,
    { baseUrl },
  );
}
