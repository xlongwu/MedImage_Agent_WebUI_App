import { afterEach, describe, expect, it, vi } from "vitest";

import {
  answerAgentTask,
  approveAgentTask,
  approveAgentTaskRecovery,
  cancelAgentTask,
  createAgentTask,
  getAgentTask,
  getAgentTaskHarness,
  listAgentTaskEvents,
  listAgentTasks,
} from "../agentTasks";

function response(body: string, ok = true): Response {
  return {
    ok,
    text: () => Promise.resolve(body),
  } as Response;
}

function mockFetch() {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockResolvedValue(response('{"schema_version":1,"items":[]}'));
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Agent Task API", () => {
  it("uses project-scoped list, detail, and cursor event endpoints", async () => {
    const fetchMock = mockFetch();
    const signal = new AbortController().signal;

    await listAgentTasks("http://api", "project / 1");
    await getAgentTask("http://api", "project / 1", "task / 1", { signal });
    await getAgentTaskHarness("http://api", "project / 1", "task / 1", { signal });
    await listAgentTaskEvents("http://api", "project / 1", "task / 1", {
      after: "cursor+/=",
      limit: 25,
      signal,
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://api/api/projects/project%20%2F%201/agent/tasks",
      expect.objectContaining({ headers: expect.any(Object) }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://api/api/projects/project%20%2F%201/agent/tasks/task%20%2F%201",
      expect.objectContaining({ signal }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://api/api/projects/project%20%2F%201/agent/tasks/task%20%2F%201/harness",
      expect.objectContaining({ signal }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://api/api/projects/project%20%2F%201/agent/tasks/task%20%2F%201/events?after=cursor%2B%2F%3D&limit=25",
      expect.objectContaining({ signal }),
    );
  });

  it("sends bounded command payloads without granular approval booleans", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("medimage", {
      getAgentApprovalToken: vi.fn().mockResolvedValue("desktop-capability-token"),
    });

    await createAgentTask("http://api", "project-1", {
      actor: "researcher",
      command_id: "command-create",
      goal: "Run preprocessing and FC",
    });
    await answerAgentTask("http://api", "project-1", "task-1", {
      answers: [{ item_id: "atlas", value: "schaefer-200" }],
      actor: "researcher",
      command_id: "command-answer",
      batch_id: "batch-atlas",
    });
    await approveAgentTask("http://api", "project-1", "task-1", {
      approval_summary_hash: "sha256:approved",
      command_id: "command-approve",
    });
    await cancelAgentTask("http://api", "project-1", "task-1", {
      actor: "researcher",
      command_id: "command-cancel",
      reason: "Goal is no longer needed",
    });

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "http://api/api/projects/project-1/agent/tasks",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          actor: "researcher",
          command_id: "command-create",
          goal: "Run preprocessing and FC",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://api/api/projects/project-1/agent/tasks/task-1/answer",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "http://api/api/projects/project-1/agent/tasks/task-1/approve",
      expect.objectContaining({
        headers: {
          "Content-Type": "application/json",
          "X-MedImage-Agent-Approval-Token": "desktop-capability-token",
        },
        body: JSON.stringify({
          approval_summary_hash: "sha256:approved",
          command_id: "command-approve",
        }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "http://api/api/projects/project-1/agent/tasks/task-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls[2]?.[1]?.body).not.toContain("confirm_");
    expect(fetchMock.mock.calls[2]?.[1]?.body).not.toContain("actor");
  });

  it("uses the desktop approval capability for recovery execution", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("medimage", {
      getAgentApprovalToken: vi.fn().mockResolvedValue("desktop-capability-token"),
    });

    await approveAgentTaskRecovery("http://api", "project-1", "task-1", {
      command_id: "command-recovery-approve",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api/api/projects/project-1/agent/tasks/task-1/approve-recovery",
      expect.objectContaining({
        headers: {
          "Content-Type": "application/json",
          "X-MedImage-Agent-Approval-Token": "desktop-capability-token",
        },
        body: JSON.stringify({ command_id: "command-recovery-approve" }),
      }),
    );
  });
});
