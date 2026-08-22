import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../../lib/api/client";
import { getLatestQcDashboardReport } from "../../lib/api/qc";
import {
  getLatestNativeFullPreprocessingRun,
  getProjectBoldReferenceReadiness,
  getProjectMotionQcReadiness,
  getProjectNiftiQcSnapshot,
} from "../../lib/api/preprocessing";
import type {
  BoldReferenceReadinessResponse,
  MotionQcReadinessResponse,
  NativeFullPreprocResponse,
  NiftiQcSnapshotResponse,
  QcDashboardReportResponse,
} from "../../types";

export type QcOverviewEvidence = {
  boldReadiness: BoldReferenceReadinessResponse | null;
  errorMessages: string[];
  loading: boolean;
  motionReadiness: MotionQcReadinessResponse | null;
  nativeRun: NativeFullPreprocResponse | null;
  niftiSnapshot: NiftiQcSnapshotResponse | null;
  qcReport: QcDashboardReportResponse | null;
  reload: () => void;
};

type QcEvidenceSnapshot = Omit<QcOverviewEvidence, "reload">;

const emptyEvidence: QcEvidenceSnapshot = {
  boldReadiness: null,
  errorMessages: [],
  loading: false,
  motionReadiness: null,
  nativeRun: null,
  niftiSnapshot: null,
  qcReport: null,
};

export function useQcEvidence(baseUrl: string, projectId: string): QcOverviewEvidence {
  const [reloadToken, setReloadToken] = useState(0);
  const reload = useCallback(() => setReloadToken((value) => value + 1), []);
  const [state, setState] = useState<{
    evidence: QcEvidenceSnapshot;
    projectId: string;
    reloadToken: number;
  }>({ evidence: emptyEvidence, projectId: "", reloadToken: -1 });
  const evidence =
    state.projectId === projectId && state.reloadToken === reloadToken
      ? state.evidence
      : { ...emptyEvidence, loading: true };

  useEffect(() => {
    let cancelled = false;
    let pendingLoads = 5;
    const update = (partial: Partial<QcEvidenceSnapshot>, errorMessage?: string | null) => {
      if (cancelled) return;
      pendingLoads -= 1;
      setState((current) => {
        const currentEvidence =
          current.projectId === projectId && current.reloadToken === reloadToken
            ? current.evidence
            : { ...emptyEvidence, loading: true };
        return {
          projectId,
          reloadToken,
          evidence: {
            ...currentEvidence,
            ...partial,
            errorMessages: errorMessage
              ? Array.from(new Set([...currentEvidence.errorMessages, errorMessage]))
              : currentEvidence.errorMessages,
            loading: pendingLoads > 0,
          },
        };
      });
    };

    void loadOptional(() => getLatestQcDashboardReport(baseUrl, projectId)).then((result) =>
      update({ qcReport: result.value }, result.error),
    );
    void loadOptional(() => getProjectNiftiQcSnapshot(baseUrl, projectId)).then((result) =>
      update({ niftiSnapshot: result.value }, result.error),
    );
    void loadOptional(() => getProjectBoldReferenceReadiness(baseUrl, projectId)).then((result) =>
      update({ boldReadiness: result.value }, result.error),
    );
    void loadOptional(() => getProjectMotionQcReadiness(baseUrl, projectId)).then((result) =>
      update({ motionReadiness: result.value }, result.error),
    );
    void loadOptional(() => getLatestNativeFullPreprocessingRun(baseUrl, projectId)).then(
      (result) => update({ nativeRun: result.value }, result.error),
    );

    return () => {
      cancelled = true;
    };
  }, [baseUrl, projectId, reloadToken]);

  return { ...evidence, reload };
}

async function loadOptional<T>(
  loader: () => Promise<T>,
): Promise<{ error: string | null; value: T | null }> {
  try {
    return { error: null, value: await loader() };
  } catch (error) {
    if (
      (error instanceof ApiError && error.status === 404) ||
      (error instanceof Error && error.message.trim() === "404")
    ) {
      return { error: null, value: null };
    }
    return {
      error: error instanceof Error ? error.message : String(error),
      value: null,
    };
  }
}
