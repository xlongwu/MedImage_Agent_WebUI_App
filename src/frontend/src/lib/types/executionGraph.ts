export type ExecutionGraphNodeState =
  | "pending"
  | "preflight"
  | "ready"
  | "running"
  | "succeeded"
  | "partial"
  | "failed"
  | "blocked"
  | "skipped"
  | "cancelled"
  | "timeout"
  | "reused"
  | "invalidated"
  | "unknown";

export type ExecutionGraphSubjectSummary = {
  total: number | null;
  observed: number;
  pending: number;
  running: number;
  succeeded: number;
  failed: number;
  skipped: number;
  blocked: number;
  cancelled: number;
  timeout: number;
  reused: number;
  invalidated: number;
  unknown: number;
};

export type ExecutionGraphNode = {
  node_id: string;
  label: string;
  backend_id: string;
  parallel_level: string;
  depends_on: string[];
  risk: "normal" | "approval" | "high" | "unknown";
  planned_input_count: number;
  planned_output_count: number;
  parameter_keys: string[];
  state: ExecutionGraphNodeState;
  state_source: "plan" | "runtime" | "summary" | "mixed" | "unknown";
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  subject_summary: ExecutionGraphSubjectSummary | null;
  warning_count: number;
  error_count: number;
  actual_output_count: number;
  current: boolean;
};

export type ExecutionGraphEdge = {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  state: "pending" | "active" | "completed" | "blocked" | "unknown";
};

export type ExecutionGraphResponse = {
  schema_version: 1;
  project_id: string;
  reviewed_plan_id: string;
  plan_hash: string;
  run_id: string | null;
  run_state: string | null;
  run_terminal: boolean;
  graph_status: "available" | "partial" | "unavailable";
  structure_hash: string;
  state_hash: string;
  generated_at: string;
  nodes: ExecutionGraphNode[];
  edges: ExecutionGraphEdge[];
  current_node_ids: string[];
  ready_node_ids: string[];
  terminal_nodes: number;
  total_nodes: number;
  node_completion_percent: number | null;
  warnings: string[];
  errors: string[];
};
