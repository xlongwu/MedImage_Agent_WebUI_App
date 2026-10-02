import type { ReactNode } from "react";

import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function TaskCard({
  dataStateLabel,
  onExplain,
  projectName,
  task,
  children,
}: {
  dataStateLabel: string;
  onExplain?: () => void;
  projectName: string;
  task: AgentTaskResponse;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const localizedAction =
    task.outcome === "canceled"
      ? t("agent.action.canceled")
      : task.next_action.type === "approve_recovery"
        ? t("agent.action.recoveryApproval")
        : task.next_action.type === "approve_execution"
          ? t("agent.action.approval")
          : task.next_action.type === "answer_science_decision" ||
              task.next_action.type === "provide_input" ||
              task.next_action.type === "revise_goal"
            ? t("agent.action.decision")
            : task.state === "preparing"
              ? t("agent.action.preparing")
              : task.state === "running" && task.progress.phase === "validation"
                ? t("agent.action.validation")
                : task.state === "waiting_for_user"
                  ? t("agent.action.waiting")
                  : task.state === "running"
                    ? t("agent.action.running")
                    : task.state === "completed"
                      ? t("agent.action.completed")
                      : task.state === "needs_attention"
                        ? t("agent.action.handoff")
                        : t("agent.action.attention");
  return (
    <Card
      className={styles.taskCard}
      role="region"
      aria-label={t("agent.taskCard.label")}
      tone="elevated"
    >
      <div className={styles.taskCardHeader}>
        <div>
          <span className={styles.eyebrow}>{t("agent.currentAction")}</span>
          <h2 tabIndex={-1}>{localizedAction}</h2>
          <p className={styles.taskGoal}>{task.goal_summary}</p>
          <p className={styles.taskContext}>
            {projectName} · {dataStateLabel}
          </p>
        </div>
        <div className={styles.taskCardMeta}>
          <Badge tone={badgeTone(task)}>
            {task.outcome === "canceled"
              ? t("agent.outcome.canceled")
              : t(`agent.state.${task.state}`)}
          </Badge>
          {onExplain ? (
            <Button onClick={onExplain} variant="ghost">
              {t("agent.taskCard.explain")}
            </Button>
          ) : null}
          <span className={styles.pulse} aria-hidden="true" />
        </div>
      </div>
      {children}
    </Card>
  );
}

function badgeTone(task: AgentTaskResponse) {
  if (task.outcome === "canceled") return "neutral" as const;
  if (task.state === "completed") return "success" as const;
  if (task.state === "needs_attention") return "danger" as const;
  if (task.state === "waiting_for_user") return "warning" as const;
  if (task.state === "running") return "info" as const;
  return "neutral" as const;
}
