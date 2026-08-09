import { Badge, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { MessageKey } from "../../../i18n/messages/en";
import type { AgentHarnessSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

const TERMINAL_REASON_KEYS: Readonly<Record<string, MessageKey>> = {
  AGENT_HARNESS_BUDGET_EXHAUSTED: "agent.harness.reason.budgetExhausted",
  AGENT_HARNESS_STEP_BUDGET_EXHAUSTED: "agent.harness.reason.stepBudgetExhausted",
  AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED: "agent.harness.reason.modelCallBudgetExhausted",
  AGENT_HARNESS_ACTION_PROPOSAL_BUDGET_EXHAUSTED: "agent.harness.reason.actionBudgetExhausted",
  AGENT_HARNESS_REPAIR_BUDGET_EXHAUSTED: "agent.harness.reason.repairBudgetExhausted",
  AGENT_HARNESS_RECOVERY_BUDGET_EXHAUSTED: "agent.harness.reason.recoveryBudgetExhausted",
  AGENT_HARNESS_INPUT_TOKEN_BUDGET_EXHAUSTED: "agent.harness.reason.inputTokenBudgetExhausted",
  AGENT_HARNESS_OUTPUT_TOKEN_BUDGET_EXHAUSTED: "agent.harness.reason.outputTokenBudgetExhausted",
  AGENT_HARNESS_WALL_TIME_BUDGET_EXHAUSTED: "agent.harness.reason.wallTimeBudgetExhausted",
  AGENT_HARNESS_CALL_OUTCOME_UNKNOWN: "agent.harness.reason.callOutcomeUnknown",
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
          steps: summary.steps_used,
          stepLimit: summary.steps_limit,
          calls: summary.model_calls_used,
          callLimit: summary.model_calls_limit,
          proposals: summary.action_proposals_used,
          proposalLimit: summary.action_proposals_limit,
          repairs: summary.repairs_used,
          repairLimit: summary.repairs_limit,
          recoveries: summary.recovery_attempts_used,
          recoveryLimit: summary.recovery_attempts_limit,
        })}
      </p>
      {summary.input_tokens_limit !== null || summary.output_tokens_limit !== null ? (
        <p>
          {t("agent.harness.tokens", {
            input: summary.input_tokens_used ?? "—",
            inputLimit: summary.input_tokens_limit ?? "—",
            output: summary.output_tokens_used ?? "—",
            outputLimit: summary.output_tokens_limit ?? "—",
          })}
        </p>
      ) : null}
      {summary.actual_provider ? (
        <p>{t("agent.harness.provider", { provider: summary.actual_provider })}</p>
      ) : null}
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
