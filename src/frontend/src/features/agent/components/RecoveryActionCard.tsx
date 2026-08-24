import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskRecoverySummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function RecoveryActionCard({
  mutating,
  onAbandon,
  onOpenDetails,
  onReopenAttention,
  recovery,
}: {
  mutating: boolean;
  onAbandon: () => Promise<void>;
  onOpenDetails: () => void;
  onReopenAttention: () => void;
  recovery: AgentTaskRecoverySummary;
}) {
  const { t } = useI18n();
  return (
    <Card className={styles.recoveryCard} tone="elevated">
      <header className={styles.recoveryHeader}>
        <div>
          <span className={styles.stepNumber}>03</span>
          <span className={styles.eyebrow}>{t("agent.nextAction")}</span>
          <h2>{t("agent.recovery.title")}</h2>
          <p>{recovery.diagnosis}</p>
        </div>
        <Badge tone="warning">{t("agent.recovery.approvalRequired")}</Badge>
      </header>
      <dl className={styles.recoveryScope}>
        <div>
          <dt>{t("agent.recovery.affected")}</dt>
          <dd>{recovery.affected_subjects.join(" · ")}</dd>
        </div>
        <div>
          <dt>{t("agent.recovery.action")}</dt>
          <dd>{recovery.recommended_action}</dd>
        </div>
        <div>
          <dt>{t("agent.recovery.untouched")}</dt>
          <dd>{recovery.untouched_scope.join(" · ")}</dd>
        </div>
        <div>
          <dt>{t("agent.recovery.replan")}</dt>
          <dd>
            {recovery.requires_new_plan
              ? t("agent.recovery.replanYes")
              : t("agent.recovery.replanNo")}
          </dd>
        </div>
      </dl>
      <div className={styles.actionFooter}>
        <Button disabled={mutating} onClick={onOpenDetails} variant="ghost">
          {t("agent.viewDetails")}
        </Button>
        <div>
          <Button
            disabled={mutating}
            onClick={() => void onAbandon().catch((): void => {})}
            variant="ghost"
          >
            {t("agent.recovery.abandon")}
          </Button>
          <Button
            data-agent-action="reopen_approve_recovery"
            data-primary-action="true"
            disabled={mutating}
            onClick={onReopenAttention}
            variant="primary"
          >
            {mutating ? t("agent.working") : t("agent.approveRecovery")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
