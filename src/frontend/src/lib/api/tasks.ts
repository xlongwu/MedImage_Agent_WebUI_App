import { getJson } from "./client";
import type {
  TaskArtifacts,
  TaskDetail,
  TaskDiagnostics,
  TaskEvent,
  TaskLogEntry,
} from "../types/task";

export function getTasks(): Promise<TaskLogEntry[]> {
  return getJson<TaskLogEntry[]>("/api/tasks");
}

export function getTask(taskId: string): Promise<TaskDetail> {
  return getJson<TaskDetail>(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function getTaskEvents(taskId: string): Promise<TaskEvent[]> {
  return getJson<TaskEvent[]>(`/api/tasks/${encodeURIComponent(taskId)}/events`);
}

export function getTaskDiagnostics(taskId: string): Promise<TaskDiagnostics> {
  return getJson<TaskDiagnostics>(`/api/tasks/${encodeURIComponent(taskId)}/diagnostics`);
}

export function getTaskArtifacts(taskId: string): Promise<TaskArtifacts> {
  return getJson<TaskArtifacts>(`/api/tasks/${encodeURIComponent(taskId)}/artifacts`);
}
