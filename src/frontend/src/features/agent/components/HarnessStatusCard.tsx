import { Badge, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { MessageKey } from "../../../i18n/messages/en";
import type { AgentHarnessSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

const TERMINAL_REASON_KEYS: Readonly<Record<string, MessageKey>> = {
  AGENT_HARNESS_BUDGET_EXHAUSTED: "agent.harness.reason.budgetExhausted",
  AGENT_HARNESS_DUPLICATE_STEP: "agent.harness.reason.duplicateStep",
  AGENT_HARNESS_PROVIDER_UNAVAILABLE: "agent.harness.reason.providerUnavailable",
  AGENT_HARNESS_MODEL_FAILED: "agent.harness.reason.modelFailed",
  AGENT_MODEL_OUTPUT_INVALID: "agent.harness.reason.invalidOutput",
  AGENT_HARNESS_STALE_ACTION: "agent.harness.reason.staleAction",
  AGENT_HARNESS_REFERENCE_DENIED: "agent.harness.reason.referenceDenied",
  AGENT_HARNESS_DRAFT_PLAN_UNAVAILABLE: "agent.harness.reason.planUnavailable",
  AGENT_HARNESS_RECOVERY_UNAVAILABLE: "agent.harness.reason.recoveryUnavailable",
  AGENT_HARNESS_STEP_FAILED: "agent.harness.reason.stepFailed",
  LIFECYCLE_TERMINAL: "agent.harness.reason.lifecycleTerminal",
  WAITING_FOR_APPROVAL: "agent.harness.reason.waitingForApproval",
  RECOVERY_PROPOSED: "agent.harness.reason.recoveryProposed",
  MODEL_FINISHED: "agent.harness.reason.modelFinished",
};

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
      {summary.next_step ? <p>{t("agent.harness.nextStep", { step: summary.next_step })}</p> : null}
      {summary.yield_count > 0 ? (
        <p>{t("agent.harness.yields", { count: summary.yield_count })}</p>
      ) : null}
      {summary.fallback_from && summary.fallback_to ? (
        <p>
          {t("agent.harness.fallback", { from: summary.fallback_from, to: summary.fallback_to })}
        </p>
      ) : null}
      {summary.latest_step_summary ? <p>{summary.latest_step_summary}</p> : null}
      {summary.terminal_reason ? (
        <p>
          {t("agent.harness.stopped", {
            reason: t(
              TERMINAL_REASON_KEYS[summary.terminal_reason] ?? "agent.harness.reason.unknown",
            ),
          })}
        </p>
      ) : null}
    </Card>
  );
}
