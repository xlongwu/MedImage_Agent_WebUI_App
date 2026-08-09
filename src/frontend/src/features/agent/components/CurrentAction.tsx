import { Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type {
  AgentTaskNextActionType,
  AgentTaskOutcome,
  AgentTaskProgress,
  AgentTaskPublicState,
} from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function CurrentAction({
  nextActionType,
  outcome,
  progress,
  state,
}: {
  nextActionType: AgentTaskNextActionType;
  outcome: AgentTaskOutcome | null;
  progress: AgentTaskProgress;
  state: AgentTaskPublicState;
}) {
  const { t } = useI18n();
  const localizedAction =
    outcome === "canceled"
      ? t("agent.action.canceled")
      : nextActionType === "approve_recovery"
        ? t("agent.action.recoveryApproval")
        : nextActionType === "approve_execution"
          ? t("agent.action.approval")
          : nextActionType === "answer_science_decision" ||
              nextActionType === "provide_input" ||
              nextActionType === "revise_goal"
            ? t("agent.action.decision")
            : state === "preparing"
              ? t("agent.action.preparing")
              : state === "running" && progress.phase === "validation"
                ? t("agent.action.validation")
                : state === "waiting_for_user"
                  ? t("agent.action.waiting")
                  : state === "running"
                    ? t("agent.action.running")
                    : state === "completed"
                      ? t("agent.action.completed")
                      : state === "needs_attention"
                        ? t("agent.action.handoff")
                        : t("agent.action.attention");
  return (
    <Card className={styles.currentAction} role="status" aria-live="polite">
      <span className={styles.stepNumber}>02</span>
      <div>
        <span className={styles.eyebrow}>{t("agent.currentAction")}</span>
        <h2 tabIndex={-1}>{localizedAction}</h2>
      </div>
      <span className={styles.pulse} aria-hidden="true" />
    </Card>
  );
}
