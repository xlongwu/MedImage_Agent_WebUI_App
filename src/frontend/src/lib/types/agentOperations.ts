export type AgentOperationalAttention = {
  code: string;
  severity: "info" | "warning" | "blocking";
  count: number;
  related_ids: string[];
};

export type AgentOperationalSummary = {
  schema_version: 1;
  project_id: string;
  window_started_at: string;
  generated_at: string;
  truncated: boolean;
  task_counts: Record<string, number>;
  model_call_counts: Record<string, number>;
  provider_failure_counts: Record<string, number>;
  scheduler_counts: Record<string, number>;
  approval_counts: Record<string, number>;
  gateway_counts: Record<string, number>;
  sandbox_counts: Record<string, number>;
  memory_status: string;
  latency_ms: Record<string, number | null>;
  attention: AgentOperationalAttention[];
};
