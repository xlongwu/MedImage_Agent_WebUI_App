import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentResultExplanation, AgentTaskResultSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import { getAgentResultMessageKey } from "./agentTaskMessages";

export function ResultSummaryCard({
  baseUrl,
  onOpenRuns,
  result,
  explanation,
}: {
  baseUrl: string;
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
            <span>{t("agent.excludedSubjects", { count: result.excluded_subjects ?? 0 })}</span>
            <span>{t("agent.totalSubjects", { count: result.total_subjects ?? 0 })}</span>
          </>
        )}
      </div>
      {result.qc_summary ? (
        <p>
          <strong>{t("agent.qcSummary")}</strong> {localizeResultText(result.qc_summary)}
        </p>
      ) : null}
      {result.artifacts.length ? (
        <div className={styles.limitations}>
          <strong>{t("agent.artifacts")}</strong>
          <ul>
            {result.artifacts.map((artifact) => (
              <li key={artifact.artifact_id}>
                {artifact.label} · {artifact.reload_status}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
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
      {result.recommended_action ? (
        <p>
          <strong>{t("agent.recommendedAction")}</strong>{" "}
          {localizeResultText(result.recommended_action)}
        </p>
      ) : null}
      <div className={styles.detailActions}>
        <Button onClick={onOpenRuns} variant="primary">
          {t("agent.viewResults")}
        </Button>
        {result.report_export_uri ? (
          <a className={styles.exportLink} href={`${baseUrl}${result.report_export_uri}`} download>
            {t("agent.exportReport")}
          </a>
        ) : (
          <button disabled title={result.export_disabled_reason ?? undefined} type="button">
            {t("agent.exportReport")}
          </button>
        )}
      </div>
      {!result.report_export_uri && result.export_disabled_reason ? (
        <small>{result.export_disabled_reason}</small>
      ) : null}
    </Card>
  );
}
