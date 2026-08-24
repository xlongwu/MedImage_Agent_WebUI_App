export interface ProjectSummary {
  id: string;
  name: string;
  study_id: string;
  modality: string;
  created_date: string;
  subjects_count: number;
  current_pipeline_id: string;
  latest_agent_task?: {
    task_id: string;
    state: "preparing" | "waiting_for_user" | "running" | "needs_attention" | "completed";
    outcome: "succeeded" | "partial" | "failed" | "canceled" | "indeterminate" | null;
    goal_summary: string;
    current_action: string;
    current_action_code: import("./agentTask").AgentTaskCurrentActionCode;
    requires_user: boolean;
    result_title: string | null;
    recent_activity: string;
    updated_at: string;
  } | null;
}

export interface ProjectDetail extends ProjectSummary {
  sequences: string[];
  scans_count: number;
  total_size: string;
  current_model_id: string;
  metadata?: {
    source?: string;
    project_dir?: string;
    rawdata_dir?: string;
    project_config_path?: string;
    dataset_index_path?: string | null;
    diagnostics?: Record<string, unknown>;
    created_at?: string;
    updated_at?: string;
    [key: string]: unknown;
  };
}

export interface StudyOverview {
  project_id: string;
  study_id: string;
  study_name: string;
  modality: string;
  sequences: string[];
  subjects: number;
  scans: number;
  total_size: string;
  date: string;
  dicom_subjects?: number;
  dicom_series?: number;
  dicom_files?: number;
}
