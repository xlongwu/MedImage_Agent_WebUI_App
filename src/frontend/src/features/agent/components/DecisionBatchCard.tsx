import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import styles from "../AgentWorkspace.module.css";

export function DecisionBatchCard({ onReopenAttention }: { onReopenAttention: () => void }) {
  const { t } = useI18n();

  return (
    <Card className={styles.nextAction} tone="elevated">
      <div className={styles.nextActionHeader}>
        <div>
          <span className={styles.stepNumber}>03</span>
          <span className={styles.eyebrow}>{t("agent.nextAction")}</span>
          <h2 tabIndex={-1}>{t("agent.decision.batch.title")}</h2>
          <p>{t("agent.decision.batch.description")}</p>
        </div>
        <Badge tone="warning">{t("agent.waitingForYou")}</Badge>
      </div>

      <p>{t("agent.decision.batch.description")}</p>
      <div className={styles.actionFooter}>
        <span>{t("agent.decision.batch.expiry")}</span>
        <Button data-primary-action="true" onClick={onReopenAttention} variant="primary">
          {t("agent.confirmation.decision.title")}
        </Button>
      </div>
    </Card>
  );
}
