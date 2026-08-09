import { useCallback, useEffect, useRef, useState } from "react";

import {
  answerAgentTask,
  approveAgentTask,
  approveAgentTaskRecovery,
  cancelAgentTask,
  createAgentTask,
  getAgentTask,
  listAgentTaskEvents,
  listAgentTasks,
} from "../../lib/api/agentTasks";
import type { AgentTaskEvent, AgentTaskResponse } from "../../lib/types/agentTask";

export type UseAgentTaskControllerOptions = {
  actor?: string;
  baseUrl: string;
  projectId: string | null;
  pollIntervalMs?: number;
};

export type AgentTaskController = {
  task: AgentTaskResponse | null;
  tasks: AgentTaskResponse[];
  events: AgentTaskEvent[];
  loading: boolean;
  mutating: boolean;
  error: string;
  create: (goal: string) => Promise<void>;
  answer: (batchId: string, answers: { item_id: string; value: string }[]) => Promise<void>;
  approve: () => Promise<void>;
  approveRecovery: () => Promise<void>;
  cancel: (reason?: string) => Promise<void>;
  dismissTask: () => void;
  refresh: () => Promise<void>;
  selectTask: (taskId: string) => Promise<void>;
};

function isPollingState(task: AgentTaskResponse | null): boolean {
  return task?.state === "preparing" || task?.state === "running";
}

function commandId(kind: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${kind}:${crypto.randomUUID()}`;
  }
  return `${kind}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
}

function errorMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === "AbortError") return "";
  return error instanceof Error ? error.message : String(error);
}

function mergeEvents(current: AgentTaskEvent[], incoming: AgentTaskEvent[]): AgentTaskEvent[] {
  if (!incoming.length) return current;
  const byId = new Map(current.map((event) => [event.event_id, event]));
  incoming.forEach((event) => byId.set(event.event_id, event));
  return Array.from(byId.values()).sort((left, right) => {
    const timeOrder = left.occurred_at.localeCompare(right.occurred_at);
    return timeOrder || left.event_id.localeCompare(right.event_id);
  });
}

export function useAgentTaskController({
  actor = "desktop-user",
  baseUrl,
  projectId,
  pollIntervalMs = 3_000,
}: UseAgentTaskControllerOptions): AgentTaskController {
  const [task, setTask] = useState<AgentTaskResponse | null>(null);
  const [tasks, setTasks] = useState<AgentTaskResponse[]>([]);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState("");
  const generationRef = useRef(0);
  const requestControllerRef = useRef<AbortController | null>(null);
  const eventCursorRef = useRef<string | null>(null);
  const selectedTaskIdRef = useRef<string | null>(null);
  const visibleTask = task?.project_id === projectId ? task : null;
  const visibleTasks = projectId ? tasks.filter((item) => item.project_id === projectId) : [];

  const beginRequest = useCallback(() => {
    requestControllerRef.current?.abort();
    const controller = new AbortController();
    requestControllerRef.current = controller;
    return controller;
  }, []);

  const loadEvents = useCallback(
    async (taskId: string, signal: AbortSignal, generation: number, reset = false) => {
      if (!projectId) return;
      const page = await listAgentTaskEvents(baseUrl, projectId, taskId, {
        after: reset ? null : eventCursorRef.current,
        limit: 100,
        signal,
      });
      if (generationRef.current !== generation || signal.aborted) return;
      eventCursorRef.current = page.next_cursor;
      setEvents((current) => (reset ? page.items : mergeEvents(current, page.items)));
    },
    [baseUrl, projectId],
  );

  const selectTask = useCallback(
    async (taskId: string) => {
      if (!projectId) return;
      const generation = generationRef.current;
      const controller = beginRequest();
      setLoading(true);
      setError("");
      selectedTaskIdRef.current = taskId;
      eventCursorRef.current = null;
      setEvents([]);
      try {
        const response = await getAgentTask(baseUrl, projectId, taskId, {
          signal: controller.signal,
        });
        if (generationRef.current !== generation || controller.signal.aborted) return;
        setTask(response);
        setTasks((current) =>
          current.some((item) => item.task_id === response.task_id)
            ? current.map((item) => (item.task_id === response.task_id ? response : item))
            : [response, ...current],
        );
        await loadEvents(taskId, controller.signal, generation, true);
      } catch (requestError) {
        if (generationRef.current === generation) setError(errorMessage(requestError));
      } finally {
        if (generationRef.current === generation) setLoading(false);
      }
    },
    [baseUrl, beginRequest, loadEvents, projectId],
  );

  const loadLatest = useCallback(async () => {
    if (!projectId) return;
    const generation = generationRef.current;
    const controller = beginRequest();
    setLoading(true);
    setError("");
    try {
      const response = await listAgentTasks(baseUrl, projectId, {
        signal: controller.signal,
      });
      if (generationRef.current !== generation || controller.signal.aborted) return;
      setTasks(response.items);
      const selected =
        response.items.find((item) => item.task_id === selectedTaskIdRef.current) ??
        response.items[0] ??
        null;
      selectedTaskIdRef.current = selected?.task_id ?? null;
      eventCursorRef.current = null;
      setEvents([]);
      setTask(selected);
      if (selected) await loadEvents(selected.task_id, controller.signal, generation, true);
    } catch (requestError) {
      if (generationRef.current === generation) setError(errorMessage(requestError));
    } finally {
      if (generationRef.current === generation) setLoading(false);
    }
  }, [baseUrl, beginRequest, loadEvents, projectId]);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    const taskId = selectedTaskIdRef.current;
    if (!taskId) {
      await loadLatest();
      return;
    }
    const generation = generationRef.current;
    const controller = beginRequest();
    try {
      const response = await getAgentTask(baseUrl, projectId, taskId, {
        signal: controller.signal,
      });
      if (generationRef.current !== generation || controller.signal.aborted) return;
      setTask(response);
      setTasks((current) =>
        current.map((item) => (item.task_id === response.task_id ? response : item)),
      );
      await loadEvents(taskId, controller.signal, generation);
      setError("");
    } catch (requestError) {
      if (generationRef.current === generation) setError(errorMessage(requestError));
    }
  }, [baseUrl, beginRequest, loadEvents, loadLatest, projectId]);

  useEffect(() => {
    generationRef.current += 1;
    requestControllerRef.current?.abort();
    selectedTaskIdRef.current = null;
    eventCursorRef.current = null;
    const timeoutId = projectId
      ? window.setTimeout(() => {
          void loadLatest();
        }, 0)
      : null;
    return () => {
      if (timeoutId !== null) window.clearTimeout(timeoutId);
      requestControllerRef.current?.abort();
    };
  }, [loadLatest, projectId]);

  useEffect(() => {
    if (!isPollingState(visibleTask)) return undefined;
    const interval = window.setInterval(() => {
      void refresh();
    }, pollIntervalMs);
    return () => window.clearInterval(interval);
  }, [pollIntervalMs, refresh, visibleTask]);

  const applyCommand = useCallback(
    async (operation: (signal: AbortSignal) => Promise<AgentTaskResponse>) => {
      const generation = generationRef.current;
      const controller = beginRequest();
      setMutating(true);
      setError("");
      try {
        const response = await operation(controller.signal);
        if (generationRef.current !== generation || controller.signal.aborted) return;
        selectedTaskIdRef.current = response.task_id;
        setTask(response);
        setTasks((current) => {
          const exists = current.some((item) => item.task_id === response.task_id);
          return exists
            ? current.map((item) => (item.task_id === response.task_id ? response : item))
            : [response, ...current];
        });
        eventCursorRef.current = null;
        setEvents([]);
        await loadEvents(response.task_id, controller.signal, generation, true);
      } catch (requestError) {
        if (generationRef.current === generation) setError(errorMessage(requestError));
        throw requestError;
      } finally {
        if (generationRef.current === generation) setMutating(false);
      }
    },
    [beginRequest, loadEvents],
  );

  const create = useCallback(
    async (goal: string) => {
      if (!projectId) return;
      await applyCommand((signal) =>
        createAgentTask(
          baseUrl,
          projectId,
          { actor, command_id: commandId("create"), goal: goal.trim() },
          { signal },
        ),
      );
    },
    [actor, applyCommand, baseUrl, projectId],
  );

  const answer = useCallback(
    async (batchId: string, answers: { item_id: string; value: string }[]) => {
      if (!projectId || !task) return;
      await applyCommand((signal) =>
        answerAgentTask(
          baseUrl,
          projectId,
          task.task_id,
          {
            actor,
            answers,
            command_id: commandId("answer"),
            batch_id: batchId,
          },
          { signal },
        ),
      );
    },
    [actor, applyCommand, baseUrl, projectId, task],
  );

  const approve = useCallback(async () => {
    if (!projectId || !task?.approval_summary?.summary_hash) {
      throw new Error("The approval summary is unavailable or stale.");
    }
    await applyCommand((signal) =>
      approveAgentTask(
        baseUrl,
        projectId,
        task.task_id,
        {
          approval_summary_hash: task.approval_summary!.summary_hash,
          command_id: commandId("approve"),
        },
        { signal },
      ),
    );
  }, [applyCommand, baseUrl, projectId, task]);

  const approveRecovery = useCallback(async () => {
    if (!projectId || !task?.recovery) {
      throw new Error("The recovery proposal is unavailable or stale.");
    }
    await applyCommand((signal) =>
      approveAgentTaskRecovery(
        baseUrl,
        projectId,
        task.task_id,
        { command_id: commandId("approve-recovery") },
        { signal },
      ),
    );
  }, [applyCommand, baseUrl, projectId, task]);

  const cancel = useCallback(
    async (reason?: string) => {
      if (!projectId || !task) return;
      await applyCommand((signal) =>
        cancelAgentTask(
          baseUrl,
          projectId,
          task.task_id,
          { actor, command_id: commandId("cancel"), reason },
          { signal },
        ),
      );
    },
    [actor, applyCommand, baseUrl, projectId, task],
  );

  const dismissTask = useCallback(() => {
    requestControllerRef.current?.abort();
    selectedTaskIdRef.current = null;
    eventCursorRef.current = null;
    setTask(null);
    setEvents([]);
    setError("");
  }, []);

  return {
    answer,
    approve,
    approveRecovery,
    cancel,
    create,
    dismissTask,
    error: projectId ? error : "",
    events: visibleTask ? events : [],
    loading: Boolean(projectId) && loading,
    mutating,
    refresh,
    selectTask,
    task: visibleTask,
    tasks: visibleTasks,
  };
}
