import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { MessageKey } from "../../../i18n/messages/en";
import type { AgentResultExplanation, AgentTaskResultSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

const RESULT_MESSAGE_KEYS: Record<string, MessageKey> = {
  "Research goal satisfied": "agent.result.satisfied.title",
  "Research goal not fully satisfied": "agent.result.notSatisfied.title",
  "Result needs attention": "agent.result.needsAttention.title",
  "The goal is supported by complete, registered, reloadable numerical evidence.":
    "agent.result.satisfied.summary",
  "Some reviewed evidence failed or remained incomplete.": "agent.result.notSatisfied.summary",
  "Evidence is incomplete, conflicting, or not reloadable.": "agent.result.needsAttention.summary",
  "Only part of the reviewed subject or artifact scope completed.":
    "agent.result.limitation.partial",
  "This is a preview result and is not a full-dataset result.":
    "agent.result.limitation.previewOnly",
  "A scientifically simplified method was used; review its limitations.":
    "agent.result.limitation.simplified",
  "Only metadata evidence exists; no declared numerical result was computed.":
    "agent.result.limitation.metadataOnly",
};

export function getAgentResultMessageKey(value: string): MessageKey | undefined {
  return RESULT_MESSAGE_KEYS[value];
}

export function ResultSummaryCard({
  onOpenRuns,
  result,
  explanation,
}: {
  onOpenRuns: () => void;
  result: AgentTaskResultSummary;
  explanation?: AgentResultExplanation | null;
}) {
  const { t } = useI18n();
  const isPlanOnly = result.artifacts.some(
    (artifact) => artifact.artifact_type === "reviewed_plan",
  );
  const localizeResultText = (value: string) => {
    const key = getAgentResultMessageKey(value);
    return key ? t(key) : value;
  };
  const title = isPlanOnly ? t("agent.planOnlyResult.title") : localizeResultText(result.title);
  const summary = isPlanOnly
    ? t("agent.planOnlyResult.summary")
    : localizeResultText(result.summary);
  const limitations = isPlanOnly
    ? [t("agent.planOnlyResult.limitation")]
    : result.limitations.map(localizeResultText);
  return (
    <Card className={styles.resultSummary}>
      <div className={styles.resultHeader}>
        <div>
          <span className={styles.stepNumber}>04</span>
          <span className={styles.eyebrow}>{t("agent.resultSummary")}</span>
          <h2>{title}</h2>
        </div>
        <Badge
          tone={
            result.outcome === "succeeded"
              ? "success"
              : result.outcome === "partial"
                ? "warning"
                : "danger"
          }
        >
          {t(`agent.outcome.${result.outcome}`)}
        </Badge>
      </div>
      <p>{summary}</p>
      {explanation?.generated_text_status === "accepted" && explanation.generated_text ? (
        <div className={styles.limitations}>
          <strong>{t("agent.result.generatedExplanation")}</strong>
          <p>{explanation.generated_text}</p>
        </div>
      ) : null}
      {explanation?.generated_text_status === "conflict_rejected" ? (
        <p className={styles.evidenceMissing}>{t("agent.result.generatedConflict")}</p>
      ) : null}
      <div className={styles.resultMetrics}>
        {isPlanOnly ? (
          <>
            <span>{t("agent.planOnlyResult.computationCount")}</span>
            <span>{t("agent.planOnlyResult.planCount", { count: result.artifacts.length })}</span>
            <span>{t("agent.planOnlyResult.executionState")}</span>
          </>
        ) : (
          <>
            <span>{t("agent.completedSubjects", { count: result.completed_subjects ?? 0 })}</span>
            <span>{t("agent.failedSubjects", { count: result.failed_subjects ?? 0 })}</span>
            <span>{t("agent.artifactCount", { count: result.artifacts.length })}</span>
          </>
        )}
      </div>
      {limitations.length ? (
        <div className={styles.limitations}>
          <strong>{t("agent.limitations")}</strong>
          <ul>
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <Button onClick={onOpenRuns} variant="secondary">
        {t("agent.openEvidence")}
      </Button>
    </Card>
  );
}
