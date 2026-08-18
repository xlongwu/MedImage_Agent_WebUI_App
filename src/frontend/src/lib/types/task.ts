export type TaskStatus =
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "pending"
  | "disconnected";

export interface TaskLogEntry {
  id: string;
  run_link_id?: string;
  reviewed_plan_id?: string;
  agent_task_id?: string | null;
  run_name: string;
  pipeline: string;
  dataset: string;
  status: TaskStatus;
  progress: number;
  started_at: string;
  duration: string;
  owner: string;
  logs: string[];
  result_path?: string | null;
  updated_at?: string;
  execution_mode?: "simulated" | "external_smoke" | "rsfmri_python";
}

export interface TaskDetail extends TaskLogEntry {
  project_id: string;
  pipeline_id: string;
  model_id: string;
  input_sequences: string[];
  output_type: string;
  updated_at: string;
}

export interface TaskStreamMessage {
  task_id: string;
  status: TaskStatus;
  progress: number;
  message: string;
  timestamp: string;
  result_path?: string | null;
}

export interface TaskEvent {
  id: number;
  task_id: string;
  status: TaskStatus;
  progress: number;
  message: string;
  timestamp: string;
  result_path?: string | null;
  source: string;
  metadata: Record<string, unknown>;
}

export interface ApprovalRecord {
  approval_id: string;
  task_id: string;
  approved: boolean;
  approved_by: string;
  approved_at: string;
  approval_scope: string;
  safety_flags: Record<string, boolean>;
}

export interface TaskDiagnostics {
  ok: boolean;
  task_id: string;
  status: TaskStatus;
  diagnosis: Array<Record<string, unknown>>;
  external_tool_results: Array<Record<string, unknown>>;
  logs: string[];
  artifacts: Record<string, unknown>;
  approval: ApprovalRecord | null;
  errors: string[];
  warnings: string[];
}

export interface TaskArtifacts {
  ok: boolean;
  task_id: string;
  result_path?: string | null;
  artifacts: Record<string, unknown>;
  approval: ApprovalRecord | null;
  errors: string[];
}
