import { Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskRecoverySummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function RecoveryApprovalReview({
  mutating,
  onApprove,
  recovery,
}: {
  mutating: boolean;
  onApprove: () => Promise<void>;
  recovery: AgentTaskRecoverySummary;
}) {
  const { t } = useI18n();
  return (
    <div className={styles.attentionReview}>
      <p>{recovery.diagnosis}</p>
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
      <div className={styles.attentionAction}>
        <Button
          data-agent-action="approve_recovery"
          data-primary-action="true"
          disabled={mutating}
          onClick={() => void onApprove().catch((): void => {})}
          variant="primary"
        >
          {mutating ? t("agent.working") : t("agent.confirmation.recovery.confirm")}
        </Button>
      </div>
    </div>
  );
}
