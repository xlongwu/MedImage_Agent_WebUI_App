import { Badge, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentHarnessSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function HarnessStatusCard({ summary }: { summary: AgentHarnessSummary }) {
  const { t } = useI18n();
  return (
    <Card className={styles.harnessStatus} aria-label={t("agent.harness.title")}>
      <div className={styles.cardHeading}>
        <div>
          <span className={styles.eyebrow}>{t("agent.harness.eyebrow")}</span>
          <h2>{t("agent.harness.title")}</h2>
        </div>
        <Badge
          tone={summary.status === "STOPPED" || summary.status === "FAILED" ? "warning" : "info"}
        >
          {t(`agent.harness.status.${summary.status}`)}
        </Badge>
      </div>
      <p>
        {t("agent.harness.budget", {
          calls: summary.model_calls_used,
          callLimit: summary.model_calls_limit,
          proposals: summary.tool_proposals_used,
          proposalLimit: summary.tool_proposals_limit,
        })}
      </p>
      {summary.latest_step_summary ? <p>{summary.latest_step_summary}</p> : null}
      {summary.terminal_reason ? (
        <p>{t("agent.harness.stopped", { reason: summary.terminal_reason })}</p>
      ) : null}
    </Card>
  );
}
