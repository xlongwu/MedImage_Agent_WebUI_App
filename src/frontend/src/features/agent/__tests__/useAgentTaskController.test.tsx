import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  approveAgentTask,
  approveAgentTaskRecovery,
  getAgentTask,
  listAgentTaskEvents,
  listAgentTasks,
} from "../../../lib/api/agentTasks";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import { useAgentTaskController } from "../useAgentTaskController";

vi.mock("../../../lib/api/agentTasks", () => ({
  answerAgentTask: vi.fn(),
  approveAgentTask: vi.fn(),
  approveAgentTaskRecovery: vi.fn(),
  cancelAgentTask: vi.fn(),
  createAgentTask: vi.fn(),
  getAgentTask: vi.fn(),
  listAgentTaskEvents: vi.fn(),
  listAgentTasks: vi.fn(),
}));

function task(
  projectId: string,
  taskId: string,
  state: AgentTaskResponse["state"],
): AgentTaskResponse {
  return {
    schema_version: 1,
    task_id: taskId,
    project_id: projectId,
    state,
    outcome: null,
    goal_summary: "Run preprocessing and FC",
    current_action: "Preparing a reviewed plan",
    next_action: {
      type: "none",
      title: "No action needed",
      description: null,
      requires_user: false,
      decision_batch_id: null,
      disabled_reason: null,
    },
    progress: {
      phase: "planning",
      percent: null,
      completed_subjects: null,
      failed_subjects: null,
      excluded_subjects: null,
      total_subjects: null,
    },
    decisions: [],
    decision_batch: null,
    approval_summary: null,
    result_summary: null,
    recovery: null,
    evidence_links: [],
    technical_details: null,
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("useAgentTaskController", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listAgentTaskEvents).mockResolvedValue({
      schema_version: 1,
      items: [],
      next_cursor: null,
    });
  });

  it("clears the old task immediately and ignores stale project responses", async () => {
    const oldResponse = deferred<Awaited<ReturnType<typeof listAgentTasks>>>();
    const projectBTask = task("project-b", "task-b", "waiting_for_user");
    vi.mocked(listAgentTasks).mockImplementation((_baseUrl, projectId) => {
      if (projectId === "project-a") return oldResponse.promise;
      return Promise.resolve({ schema_version: 1, items: [projectBTask], total: 1 });
    });

    const { result, rerender } = renderHook(
      ({ projectId }) =>
        useAgentTaskController({ baseUrl: "http://api", projectId, pollIntervalMs: 60_000 }),
      { initialProps: { projectId: "project-a" } },
    );

    rerender({ projectId: "project-b" });
    expect(result.current.task).toBeNull();
    await waitFor(() => expect(result.current.task?.task_id).toBe("task-b"));

    await act(async () => {
      oldResponse.resolve({
        schema_version: 1,
        items: [task("project-a", "task-a", "running")],
        total: 1,
      });
      await oldResponse.promise;
    });

    expect(result.current.task?.project_id).toBe("project-b");
    expect(result.current.task?.task_id).toBe("task-b");
  });

  it("approves only with the server-issued summary hash", async () => {
    const approvalTask: AgentTaskResponse = {
      ...task("project-a", "task-a", "waiting_for_user"),
      approval_summary: {
        summary_hash: "sha256:summary",
        goal: "Run preprocessing and FC",
        dataset_summary: "3 subjects",
        execution_summary: "Reviewed native pipeline",
        write_roots: ["project://derivatives"],
        rawdata_read_only: true,
        external_tools: [],
        limitations: [],
        science_changes: [],
        sections: [],
        expires_at: null,
      },
    };
    vi.mocked(listAgentTasks).mockResolvedValue({
      schema_version: 1,
      items: [approvalTask],
      total: 1,
    });
    vi.mocked(approveAgentTask).mockResolvedValue({ ...approvalTask, state: "running" });
    vi.mocked(getAgentTask).mockResolvedValue({ ...approvalTask, state: "running" });

    const { result } = renderHook(() =>
      useAgentTaskController({
        actor: "researcher",
        baseUrl: "http://api",
        projectId: "project-a",
        pollIntervalMs: 60_000,
      }),
    );
    await waitFor(() => expect(result.current.task?.task_id).toBe("task-a"));

    await act(async () => {
      await result.current.approve();
    });

    expect(approveAgentTask).toHaveBeenCalledWith(
      "http://api",
      "project-a",
      "task-a",
      expect.objectContaining({
        approval_summary_hash: "sha256:summary",
      }),
      expect.any(Object),
    );
    expect(vi.mocked(approveAgentTask).mock.calls[0][3]).not.toHaveProperty("actor");
  });

  it("polls only active tasks and resumes event pagination without duplicates", async () => {
    const runningTask = task("project-a", "task-a", "running");
    vi.mocked(listAgentTasks).mockResolvedValue({
      schema_version: 1,
      items: [runningTask],
      total: 1,
    });
    vi.mocked(getAgentTask).mockResolvedValue(runningTask);
    vi.mocked(listAgentTaskEvents)
      .mockResolvedValueOnce({
        schema_version: 1,
        items: [
          {
            event_id: "event-1",
            task_id: "task-a",
            project_id: "project-a",
            source: "lifecycle",
            type: "running",
            occurred_at: "2026-07-16T00:00:00Z",
            title: "Started",
            summary: "Started",
            evidence_uri: null,
          },
        ],
        next_cursor: "cursor-1",
      })
      .mockResolvedValue({
        schema_version: 1,
        items: [
          {
            event_id: "event-1",
            task_id: "task-a",
            project_id: "project-a",
            source: "lifecycle",
            type: "running",
            occurred_at: "2026-07-16T00:00:00Z",
            title: "Started",
            summary: "Started",
            evidence_uri: null,
          },
          {
            event_id: "event-2",
            task_id: "task-a",
            project_id: "project-a",
            source: "run",
            type: "progress",
            occurred_at: "2026-07-16T00:01:00Z",
            title: "Progress",
            summary: "One subject completed",
            evidence_uri: "project://runs/run-1",
          },
        ],
        next_cursor: "cursor-2",
      });

    const { result } = renderHook(() =>
      useAgentTaskController({ baseUrl: "http://api", projectId: "project-a", pollIntervalMs: 15 }),
    );

    await waitFor(() => expect(getAgentTask).toHaveBeenCalled());
    await waitFor(() => expect(result.current.events).toHaveLength(2));
    expect(listAgentTaskEvents).toHaveBeenCalledWith(
      "http://api",
      "project-a",
      "task-a",
      expect.objectContaining({ after: "cursor-1" }),
    );
    expect(result.current.events.map((event) => event.event_id)).toEqual(["event-1", "event-2"]);
  });

  it("does not poll while a task is waiting for a user decision", async () => {
    vi.mocked(listAgentTasks).mockResolvedValue({
      schema_version: 1,
      items: [task("project-a", "task-a", "waiting_for_user")],
      total: 1,
    });

    const { result } = renderHook(() =>
      useAgentTaskController({ baseUrl: "http://api", projectId: "project-a", pollIntervalMs: 10 }),
    );
    await waitFor(() => expect(result.current.task?.state).toBe("waiting_for_user"));
    await new Promise((resolve) => window.setTimeout(resolve, 35));

    expect(getAgentTask).not.toHaveBeenCalled();
  });

  it("replaces running state with the reconciled needs-attention terminal state", async () => {
    const runningTask = task("project-a", "task-a", "running");
    const failedTask = {
      ...runningTask,
      state: "needs_attention" as const,
      current_action: "The run needs review",
    };
    vi.mocked(listAgentTasks).mockResolvedValue({
      schema_version: 1,
      items: [runningTask],
      total: 1,
    });
    vi.mocked(getAgentTask).mockResolvedValue(failedTask);

    const { result } = renderHook(() =>
      useAgentTaskController({ baseUrl: "http://api", projectId: "project-a", pollIntervalMs: 10 }),
    );

    await waitFor(() => expect(result.current.task?.state).toBe("needs_attention"));
    expect(result.current.task?.current_action).toBe("The run needs review");
  });

  it("uses the bounded recovery command without exposing candidate scope", async () => {
    const recoveryTask: AgentTaskResponse = {
      ...task("project-a", "task-a", "waiting_for_user"),
      next_action: {
        type: "approve_recovery",
        title: "Approve recovery",
        description: null,
        requires_user: true,
        decision_batch_id: null,
        disabled_reason: null,
      },
      recovery: {
        proposal_id: "proposal-1",
        diagnosis: "One subject failed",
        affected_subjects: ["sub-02"],
        recommended_action: "Retry only the failed subject",
        untouched_scope: ["sub-01", "sub-03"],
        requires_new_plan: false,
        approval_summary_hash: "recovery-hash",
      },
    };
    vi.mocked(listAgentTasks).mockResolvedValue({
      schema_version: 1,
      items: [recoveryTask],
      total: 1,
    });
    vi.mocked(approveAgentTaskRecovery).mockResolvedValue({
      ...recoveryTask,
      state: "running",
    });

    const { result } = renderHook(() =>
      useAgentTaskController({
        actor: "researcher",
        baseUrl: "http://api",
        projectId: "project-a",
        pollIntervalMs: 60_000,
      }),
    );
    await waitFor(() => expect(result.current.task?.recovery?.proposal_id).toBe("proposal-1"));

    await act(async () => {
      await result.current.approveRecovery();
    });

    expect(approveAgentTaskRecovery).toHaveBeenCalledWith(
      "http://api",
      "project-a",
      "task-a",
      expect.objectContaining({ command_id: expect.any(String) }),
      expect.any(Object),
    );
    const body = vi.mocked(approveAgentTaskRecovery).mock.calls[0][3];
    expect(body).not.toHaveProperty("actor");
    expect(body).not.toHaveProperty("candidate_id");
    expect(body).not.toHaveProperty("node_ids");
    expect(body).not.toHaveProperty("subject_ids");
  });
});
