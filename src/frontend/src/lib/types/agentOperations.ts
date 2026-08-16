export type AgentOperationalAttention = {
  code: string;
  severity: "warning" | "blocking";
  count: number;
};

export type AgentOperationalSummary = {
  project_id: string;
  window_hours: number;
  generated_at: string;
  lifecycle_state_counts: Record<string, number>;
  model_call_counts: Record<string, number>;
  approval_waiting_count: number;
  attentions: AgentOperationalAttention[];
  truncated: boolean;
};
