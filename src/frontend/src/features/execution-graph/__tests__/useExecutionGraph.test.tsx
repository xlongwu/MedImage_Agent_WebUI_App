import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getPlanExecutionGraph } from "../../../lib/api/executionGraphs";
import type { ExecutionGraphResponse } from "../../../lib/types/executionGraph";
import { useExecutionGraph } from "../useExecutionGraph";

vi.mock("../../../lib/api/executionGraphs", () => ({
  getPlanExecutionGraph: vi.fn(),
  getRunExecutionGraph: vi.fn(),
  previewExecutionGraph: vi.fn(),
}));

const response = (reviewedPlanId: string): ExecutionGraphResponse =>
  ({
    project_id: "project-1",
    reviewed_plan_id: reviewedPlanId,
    run_id: null,
    run_terminal: true,
    current_node_ids: [],
    nodes: [],
    edges: [],
  }) as ExecutionGraphResponse;

describe("useExecutionGraph", () => {
  beforeEach(() => vi.clearAllMocks());

  it("accepts a plan graph only when reviewed_plan_id matches the request", async () => {
    vi.mocked(getPlanExecutionGraph).mockResolvedValue(response("plan-1"));

    const { result } = renderHook(() =>
      useExecutionGraph({
        baseUrl: "http://api",
        projectId: "project-1",
        reviewedPlanId: "plan-1",
      }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.graph?.reviewed_plan_id).toBe("plan-1");
  });

  it("discards a graph returned for a different reviewed plan", async () => {
    vi.mocked(getPlanExecutionGraph).mockResolvedValue(response("plan-other"));

    const { result } = renderHook(() =>
      useExecutionGraph({
        baseUrl: "http://api",
        projectId: "project-1",
        reviewedPlanId: "plan-1",
      }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.graph).toBeNull();
  });

  it("clears the previous project graph immediately when project context is removed", async () => {
    vi.mocked(getPlanExecutionGraph).mockResolvedValue(response("plan-1"));

    const { result, rerender } = renderHook(
      ({ projectId }: { projectId: string | null }) =>
        useExecutionGraph({
          baseUrl: "http://api",
          projectId,
          reviewedPlanId: "plan-1",
        }),
      { initialProps: { projectId: "project-1" as string | null } },
    );
    await waitFor(() => expect(result.current.graph?.reviewed_plan_id).toBe("plan-1"));

    rerender({ projectId: null });

    expect(result.current.graph).toBeNull();
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });
});
