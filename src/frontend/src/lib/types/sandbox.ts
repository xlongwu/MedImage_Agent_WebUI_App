export type SandboxAttemptStatus =
  | "PREPARING"
  | "PREPARED"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "BLOCKED"
  | "TIMED_OUT"
  | "CANCELLED"
  | "INTERRUPTED";

export interface SandboxAttempt {
  sandbox_id: string;
  run_id: string;
  node_id: string;
  subject_id: string | null;
  status: SandboxAttemptStatus;
  started_at: string | null;
  ended_at: string | null;
  result_code: string | null;
  output_count: number;
  policy_version: string;
  network_isolation: "not_enforced";
}

export interface SandboxAttemptsResponse {
  ok: boolean;
  project_id: string;
  run_id: string;
  sandbox_attempts: SandboxAttempt[];
}
