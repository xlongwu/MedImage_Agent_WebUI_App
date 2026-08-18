import type { AgentTaskResponse } from "../../lib/types/agentTask";

export type AttentionAction = {
  key: string;
  type: "decision" | "approval" | "recovery";
};

export function isDecisionAction(task: AgentTaskResponse): boolean {
  return (
    task.decision_batch !== null &&
    (task.next_action.type === "answer_science_decision" ||
      task.next_action.type === "provide_input" ||
      task.next_action.type === "revise_goal")
  );
}

/**
 * This identity deliberately lives only in the browser. It lets the UI dismiss
 * one current request without persisting UI state or changing an approval hash.
 */
export function attentionActionFor(task: AgentTaskResponse | null): AttentionAction | null {
  if (!task || !task.next_action.requires_user) return null;

  if (isDecisionAction(task) && task.decision_batch) {
    return {
      key: `decision:${task.task_id}:${task.decision_batch.batch_id}:${task.decision_batch.evidence_snapshot_hash}`,
      type: "decision",
    };
  }

  if (task.next_action.type === "approve_execution" && task.approval_summary?.summary_hash) {
    return {
      key: `approval:${task.task_id}:${task.approval_summary.summary_hash}`,
      type: "approval",
    };
  }

  if (task.next_action.type === "approve_recovery" && task.recovery) {
    return {
      key: `recovery:${task.task_id}:${task.recovery.proposal_id}:${task.recovery.approval_summary_hash ?? "missing"}`,
      type: "recovery",
    };
  }

  return null;
}
