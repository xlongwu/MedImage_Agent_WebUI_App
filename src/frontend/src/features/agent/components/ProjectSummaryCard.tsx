import { Badge, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import { getAgentResultMessageKey } from "./agentTaskMessages";

export function ProjectSummaryCard({
  dataStateLabel,
  projectName,
  task,
}: {
  dataStateLabel: string;
  projectName: string;
  task: AgentTaskResponse | null;
}) {
  const { t } = useI18n();
  const issueCount =
    (task?.progress.failed_subjects ?? 0) + (task?.next_action.requires_user ? 1 : 0);
  const planOnlyResult = Boolean(
    task?.result_summary?.artifacts.some((artifact) => artifact.artifact_type === "reviewed_plan"),
  );
  const recentResult = planOnlyResult
    ? t("agent.planOnlyResult.title")
    : task?.result_summary?.title
      ? getAgentResultMessageKey(task.result_summary.title)
        ? t(getAgentResultMessageKey(task.result_summary.title)!)
        : task.result_summary.title
      : t("agent.noResult");

  return (
    <Card className={styles.projectSummary} tone="elevated">
      <div>
        <span className={styles.eyebrow}>{t("agent.projectSummary")}</span>
        <h2>{projectName}</h2>
        <p>{dataStateLabel}</p>
      </div>
      <dl className={styles.summaryMetrics}>
        <div>
          <dt>{t("agent.taskStatus")}</dt>
          <dd>
            <Badge tone={stateTone(task?.state)}>
              {task ? t(`agent.state.${task.state}`) : t("agent.noTask")}
            </Badge>
          </dd>
        </div>
        <div>
          <dt>{t("agent.recentResult")}</dt>
          <dd>{recentResult}</dd>
        </div>
        <div>
          <dt>{t("agent.attentionItems")}</dt>
          <dd>{issueCount}</dd>
        </div>
      </dl>
    </Card>
  );
}

function stateTone(state: AgentTaskResponse["state"] | undefined) {
  if (state === "completed") return "success" as const;
  if (state === "needs_attention") return "danger" as const;
  if (state === "waiting_for_user") return "warning" as const;
  if (state === "running") return "info" as const;
  return "neutral" as const;
}
