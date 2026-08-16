import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getProjectRun,
  getProjectRunStateTimeline,
  listProjectRunArtifacts,
  listProjectRunEvents,
  listProjectRunLogs,
} from "../../lib/api/projectRuns";
import { listSandboxAttempts } from "../../lib/api/sandboxes";
import type { SandboxAttemptsResponse } from "../../lib/types/sandbox";
import type {
  ProjectRunArtifactsResponse,
  ProjectRunDetailResponse,
  ProjectRunEventsResponse,
  ProjectRunLogsResponse,
  ProjectRunStateTimelineResponse,
} from "../../types";

export type ProjectRunDetails = {
  artifacts: ProjectRunArtifactsResponse;
  detail: ProjectRunDetailResponse;
  events: ProjectRunEventsResponse;
  logs: ProjectRunLogsResponse;
  timeline: ProjectRunStateTimelineResponse;
  sandboxAttempts: SandboxAttemptsResponse;
};

type ProjectRunDetailsState = {
  data: ProjectRunDetails | null;
  error: string;
  key: string;
  loading: boolean;
};

function selectionKey(projectId: string | null, runId: string | null): string {
  return projectId && runId ? `${projectId}\u0000${runId}` : "";
}

function assertProjectRunResponse(
  projectId: string,
  runId: string,
  response: ProjectRunDetails,
): void {
  const projectIds = [
    response.detail.run_link.project_id,
    response.events.project_id,
    response.logs.project_id,
    response.artifacts.project_id,
    response.timeline.project_id,
    response.sandboxAttempts.project_id,
  ];
  const runIds = [
    response.detail.run_link.run_id,
    response.events.run_id,
    response.logs.run_id,
    response.artifacts.run_id,
    response.timeline.run_id,
    response.sandboxAttempts.run_id,
  ];
  if (projectIds.some((value) => value !== projectId) || runIds.some((value) => value !== runId)) {
    throw new Error("Project run response did not match the selected project and run.");
  }
}

export function useProjectRunDetails(
  baseUrl: string,
  projectId: string | null,
  runId: string | null,
) {
  const currentKey = useMemo(() => selectionKey(projectId, runId), [projectId, runId]);
  const requestVersion = useRef(0);
  const [state, setState] = useState<ProjectRunDetailsState>({
    data: null,
    error: "",
    key: "",
    loading: false,
  });

  const reload = useCallback(async () => {
    const version = ++requestVersion.current;
    const key = selectionKey(projectId, runId);
    if (!projectId || !runId) {
      setState({ data: null, error: "", key, loading: false });
      return null;
    }

    setState((current) => ({
      data: current.key === key ? current.data : null,
      error: "",
      key,
      loading: true,
    }));
    try {
      const [detail, events, logs, artifacts, timeline, sandboxAttempts] = await Promise.all([
        getProjectRun(baseUrl, projectId, runId),
        listProjectRunEvents(baseUrl, projectId, runId),
        listProjectRunLogs(baseUrl, projectId, runId, {
          includeContent: true,
          maxBytes: 20000,
        }),
        listProjectRunArtifacts(baseUrl, projectId, runId),
        getProjectRunStateTimeline(baseUrl, projectId, runId),
        listSandboxAttempts(baseUrl, projectId, runId),
      ]);
      const data = { artifacts, detail, events, logs, timeline, sandboxAttempts };
      assertProjectRunResponse(projectId, runId, data);
      if (requestVersion.current === version) {
        setState({ data, error: "", key, loading: false });
      }
      return data;
    } catch (reason) {
      if (requestVersion.current === version) {
        setState({
          data: null,
          error: reason instanceof Error ? reason.message : String(reason),
          key,
          loading: false,
        });
      }
      return null;
    }
  }, [baseUrl, projectId, runId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- Load the selected backend-owned run bundle when its identity changes.
    void reload();
    return () => {
      requestVersion.current += 1;
    };
  }, [reload]);

  const visibleState =
    state.key === currentKey
      ? state
      : { data: null, error: "", key: currentKey, loading: Boolean(currentKey) };

  return { ...visibleState, reload };
}
