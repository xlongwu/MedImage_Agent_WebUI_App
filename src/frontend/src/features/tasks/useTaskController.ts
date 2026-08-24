"use client";
import { useCallback, useEffect, useMemo } from "react";
import type {
  TaskDiagnostics,
  TaskEvent,
  TaskLogEntry,
  TaskStreamMessage,
} from "../../lib/types/task";
import { useTaskStream } from "../../hooks/useTaskStream";
import { useTasks } from "../../hooks/useTasks";
import { useTaskEvents } from "../../hooks/useTaskEvents";
import { useTaskDiagnostics } from "../../hooks/useTaskDiagnostics";

const noopTaskSelection = () => {};

export interface TaskController {
  tasks: TaskLogEntry[];
  tasksLoading: boolean;
  tasksError: string;
  reloadTasks: () => Promise<TaskLogEntry[] | void>;
  updateTaskFromStream: ReturnType<typeof useTasks>["updateTaskFromStream"];
  selectedTaskId: string | null;
  setSelectedTaskId: (id: string | null) => void;
  selectedTask: TaskLogEntry | null;
  taskCounts: { completed: number; running: number; failed: number };
  hasPreprocessingRun: boolean;
  latestPreprocessingRunId: string | null;
  taskEvents: TaskEvent[];
  taskEventsLoading: boolean;
  taskEventsError: string;
  reloadTaskEvents: () => Promise<TaskEvent[] | void>;
  taskDiagnosticsData: TaskDiagnostics;
  reloadTaskDiagnostics: () => Promise<TaskDiagnostics | void>;
  taskStreamConnected: boolean;
  taskStreamError: string | null;
  handleReconnectTaskStream: () => void;
  taskEventsSetData: (updater: (current: TaskEvent[]) => TaskEvent[]) => void;
}

export function useTaskController(
  selectedTaskId: string | null = null,
  setSelectedTaskId: ((id: string | null) => void) | undefined = undefined,
  setActiveTaskId: ((id: string | null) => void) | undefined = undefined,
): TaskController {
  const setSelectedTaskIdSafe = setSelectedTaskId ?? noopTaskSelection;
  const setActiveTaskIdSafe = setActiveTaskId ?? noopTaskSelection;
  const tasks = useTasks();
  const taskEvents = useTaskEvents(selectedTaskId);
  const taskDiagnostics = useTaskDiagnostics(selectedTaskId);
  const updateTaskFromStream = tasks.updateTaskFromStream;
  const setTaskEventsData = taskEvents.setData;
  const reloadTaskEvents = taskEvents.reload;
  const reloadTaskDiagnostics = taskDiagnostics.reload;

  const handleTaskMessage = useCallback(
    (message: TaskStreamMessage) => {
      updateTaskFromStream(message);
      if (selectedTaskId === message.task_id) {
        const event: TaskEvent = {
          id: Date.now(),
          task_id: message.task_id,
          status: message.status,
          progress: message.progress,
          message: message.message,
          timestamp: message.timestamp,
          result_path: message.result_path,
          source: "websocket",
          metadata: {},
        };
        setTaskEventsData((current) => [...current, event]);
      }
      // notice is owned by the app controller; callers handle it.
      if (
        (message.status === "completed" || message.status === "failed") &&
        selectedTaskId === message.task_id
      ) {
        window.setTimeout(() => {
          reloadTaskEvents();
          reloadTaskDiagnostics();
        }, 250);
      }
    },
    [
      selectedTaskId,
      reloadTaskDiagnostics,
      reloadTaskEvents,
      setTaskEventsData,
      updateTaskFromStream,
    ],
  );

  const taskStream = useTaskStream(null, handleTaskMessage);

  // Reconnect stream when selectedTaskId changes.
  useEffect(() => {
    const nextTaskId = selectedTaskId;
    if (!nextTaskId) return;
    setActiveTaskIdSafe(null);
    window.setTimeout(() => setActiveTaskIdSafe(nextTaskId), 0);
    // We intentionally only depend on selectedTaskId here; setActiveTaskIdSafe is
    // passed in from the parent so we don't list it as a dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTaskId]);

  const selectedTask = useMemo(
    () => tasks.data.find((task) => task.id === selectedTaskId) ?? null,
    [selectedTaskId, tasks.data],
  );

  const taskCounts = useMemo(() => {
    const completed = tasks.data.filter((task) => task.status === "completed").length;
    const running = tasks.data.filter((task) => task.status === "running").length;
    const failed = tasks.data.filter((task) => task.status === "failed").length;
    return { completed, running, failed };
  }, [tasks.data]);

  const hasPreprocessingRun = useMemo(() => {
    return tasks.data.some(
      (task) =>
        task.pipeline?.toLowerCase().includes("preprocess") ||
        task.run_name?.toLowerCase().includes("preprocess"),
    );
  }, [tasks.data]);

  const latestPreprocessingRunId = useMemo(() => {
    for (const task of tasks.data) {
      const candidate = preprocessingRunIdFromTask(task);
      if (candidate) return candidate;
    }
    return null;
  }, [tasks.data]);

  const handleReconnectTaskStream = useCallback(() => {
    const nextTaskId = selectedTaskId;
    if (!nextTaskId) return;
    setActiveTaskIdSafe(null);
    window.setTimeout(() => setActiveTaskIdSafe(nextTaskId), 0);
  }, [selectedTaskId, setActiveTaskIdSafe]);

  return {
    tasks: tasks.data,
    tasksLoading: tasks.loading,
    tasksError: tasks.error,
    reloadTasks: tasks.reload,
    updateTaskFromStream,
    selectedTaskId,
    setSelectedTaskId: setSelectedTaskIdSafe,
    selectedTask,
    taskCounts,
    hasPreprocessingRun,
    latestPreprocessingRunId,
    taskEvents: taskEvents.data,
    taskEventsLoading: taskEvents.loading,
    taskEventsError: taskEvents.error,
    reloadTaskEvents,
    taskDiagnosticsData: taskDiagnostics.data,
    reloadTaskDiagnostics,
    taskStreamConnected: taskStream.connected,
    taskStreamError: taskStream.error,
    handleReconnectTaskStream,
    taskEventsSetData: setTaskEventsData,
  };
}

function preprocessingRunIdFromTask(task: TaskLogEntry): string | null {
  const searchable = [task.id, task.run_name, task.pipeline, task.result_path ?? ""].join(" ");
  if (!/preprocess/i.test(searchable)) {
    return null;
  }
  const pathMatch = searchable.match(/preprocessing_runs[\\/]+([A-Za-z0-9_-]+)/i);
  if (pathMatch?.[1]) {
    return pathMatch[1];
  }
  const ppMatch = searchable.match(/\b(pp-[A-Za-z0-9_-]+)\b/i);
  return ppMatch?.[1] ?? null;
}
