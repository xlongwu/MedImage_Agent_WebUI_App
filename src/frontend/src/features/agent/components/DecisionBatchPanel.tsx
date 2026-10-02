import { Badge, Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import styles from "../AgentWorkspace.module.css";

export function DecisionBatchPanel({ onReopenAttention }: { onReopenAttention: () => void }) {
  const { t } = useI18n();

  return (
    <section className={styles.taskAction}>
      <div className={styles.taskPanelHeader}>
        <div>
          <span className={styles.eyebrow}>{t("agent.nextAction")}</span>
          <h3 tabIndex={-1}>{t("agent.decision.batch.title")}</h3>
          <p>{t("agent.decision.batch.description")}</p>
        </div>
        <Badge tone="warning">{t("agent.waitingForYou")}</Badge>
      </div>

      <div className={styles.actionFooter}>
        <span>{t("agent.decision.batch.expiry")}</span>
        <Button data-primary-action="true" onClick={onReopenAttention} variant="primary">
          {t("agent.confirmation.decision.title")}
        </Button>
      </div>
    </section>
  );
}
