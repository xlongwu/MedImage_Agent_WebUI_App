import { useCallback, useEffect, useRef, useState } from "react";

import {
  getPlanExecutionGraph,
  getRunExecutionGraph,
  previewExecutionGraph,
} from "../../lib/api/executionGraphs";
import type { ExecutionGraphResponse } from "../../lib/types/executionGraph";

export type ExecutionGraphRequest = {
  baseUrl: string;
  projectId: string | null;
  reviewedPlanId?: string | null;
  runId?: string | null;
  previewPlan?: Record<string, unknown> | null;
  autoRefresh?: boolean;
};

export function useExecutionGraph(request: ExecutionGraphRequest) {
  const [graph, setGraph] = useState<ExecutionGraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const active = useRef<AbortController | null>(null);
  const retryDelay = useRef(3000);

  const refresh = useCallback(() => setReload((value) => value + 1), []);
  useEffect(() => {
    active.current?.abort();
    if (!request.projectId || (!request.runId && !request.reviewedPlanId && !request.previewPlan)) {
      setGraph(null);
      setError(null);
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    active.current = controller;
    let timer: number | undefined;
    let disposed = false;
    const load = async () => {
      setLoading((value) => value || !graph);
      try {
        const response = request.runId
          ? await getRunExecutionGraph(
              request.baseUrl,
              request.projectId!,
              request.runId,
              controller.signal,
            )
          : request.reviewedPlanId
            ? await getPlanExecutionGraph(
                request.baseUrl,
                request.projectId!,
                request.reviewedPlanId,
                controller.signal,
              )
            : await previewExecutionGraph(
                request.baseUrl,
                request.projectId!,
                request.previewPlan!,
                controller.signal,
              );
        if (
          disposed ||
          response.project_id !== request.projectId ||
          (request.runId && response.run_id !== request.runId)
        )
          return;
        setGraph(response);
        setError(null);
        retryDelay.current = 3000;
        if (request.autoRefresh && request.runId && !response.run_terminal)
          timer = window.setTimeout(load, 3000);
      } catch (reason) {
        if (disposed || controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        if (request.autoRefresh && request.runId) {
          const delay = retryDelay.current;
          retryDelay.current = Math.min(15000, delay * 2);
          timer = window.setTimeout(load, delay);
        }
      } finally {
        if (!disposed) setLoading(false);
      }
    };
    void load();
    return () => {
      disposed = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
    // graph intentionally excluded: completed requests schedule serially without stale closure updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    request.baseUrl,
    request.projectId,
    request.previewPlan,
    request.reviewedPlanId,
    request.runId,
    request.autoRefresh,
    reload,
  ]);
  return { graph, error, loading, refresh };
}
