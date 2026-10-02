import { Badge, Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentResultExplanation, AgentTaskResultSummary } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import {
  RESULT_SUMMARY_KEYS,
  getAgentExportDisabledKey,
  getAgentLimitationKey,
  getAgentRecommendedActionKey,
} from "./agentTaskMessages";

export function TaskResultPanel({
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
  const isPlanOnly = result.summary_code === "result.plan_only";
  const summaryKeys = RESULT_SUMMARY_KEYS[result.summary_code];
  const title = t(summaryKeys.title);
  const summary = t(summaryKeys.summary);
  const limitations = isPlanOnly
    ? [t("agent.planOnlyResult.limitation")]
    : result.limitation_codes.map((code) => {
        const key = getAgentLimitationKey(code);
        return key ? t(key) : code;
      });
  const recommendedActionKey = getAgentRecommendedActionKey(result.recommended_action_code);
  const exportDisabledKey = getAgentExportDisabledKey(result.export_disabled_code);
  return (
    <section className={styles.taskResult}>
      <div className={styles.taskPanelHeader}>
        <div>
          <span className={styles.eyebrow}>{t("agent.resultSummary")}</span>
          <h3>{title}</h3>
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
      <p>
        <strong>{t("agent.qcSummary")}</strong>{" "}
        {result.validation_checks_passed === null
          ? t("agent.validation.noRecord")
          : t("agent.validation.checks", {
              passed: result.validation_checks_passed,
              failed: result.validation_checks_failed ?? 0,
            })}
      </p>
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
      {recommendedActionKey ? (
        <p>
          <strong>{t("agent.recommendedAction")}</strong> {t(recommendedActionKey)}
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
          <button
            disabled
            title={exportDisabledKey ? t(exportDisabledKey) : undefined}
            type="button"
          >
            {t("agent.exportReport")}
          </button>
        )}
      </div>
      {!result.report_export_uri && exportDisabledKey ? (
        <small>{t(exportDisabledKey)}</small>
      ) : null}
    </section>
  );
}
