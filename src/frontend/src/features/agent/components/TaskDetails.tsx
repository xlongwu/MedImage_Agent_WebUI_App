import { Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentHarnessActivityPage, AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import { TechnicalEvidence } from "./TechnicalEvidence";

export function TaskDetails({
  advancedMode,
  harnessActivity,
  onLoadHarnessActivity,
  onOpenRuns,
  task,
}: {
  advancedMode: boolean;
  harnessActivity: AgentHarnessActivityPage | null;
  onLoadHarnessActivity: () => Promise<void>;
  onOpenRuns: () => void;
  task: AgentTaskResponse;
}) {
  const { t } = useI18n();
  return (
    <details
      className={styles.taskDetails}
      onToggle={(event) => {
        if (advancedMode && event.currentTarget.open && harnessActivity === null) {
          void onLoadHarnessActivity();
        }
      }}
    >
      <summary>{t("agent.taskDetails")}</summary>
      <div className={styles.taskDetailsBody}>
        <section>
          <h3>{t("agent.evidence")}</h3>
          {task.evidence_links.length ? (
            <ul className={styles.evidenceList}>
              {task.evidence_links.map((link) => (
                <li key={link.id}>
                  <BadgeLike available={link.available} />
                  <span>{link.label}</span>
                  <code>{link.uri}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t("agent.noEvidence")}</p>
          )}
          <div className={styles.detailActions}>
            <Button onClick={onOpenRuns} variant="secondary">
              {t("agent.openRuns")}
            </Button>
          </div>
        </section>
        {advancedMode && task.technical_details ? (
          <TechnicalEvidence details={task.technical_details} />
        ) : null}
        {advancedMode && harnessActivity ? (
          <section className={styles.harnessActivity} aria-label={t("agent.harness.activity")}>
            <h3>{t("agent.harness.activity")}</h3>
            <p>{t("agent.harness.integrity", { status: harnessActivity.integrity_status })}</p>
            {harnessActivity.stop_reason ? (
              <p>{t("agent.harness.stopReason", { reason: harnessActivity.stop_reason })}</p>
            ) : null}
            {harnessActivity.entries.length ? (
              <ol>
                {harnessActivity.entries.map((entry) => (
                  <li key={entry.step_id}>
                    <strong>{t("agent.harness.step", { number: entry.step_no })}</strong>
                    <span>{entry.action_kind ?? t("agent.harness.noAction")}</span>
                    <span>{entry.validation_result}</span>
                    {entry.action_result_code ? <span>{entry.action_result_code}</span> : null}
                    {entry.model_calls.map((call) => (
                      <small key={call.call_id}>
                        {t("agent.harness.call", {
                          provider: call.provider,
                          phase: call.phase,
                          status: call.status,
                        })}
                      </small>
                    ))}
                    {entry.references.length ? (
                      <small>{t("agent.harness.refs", { count: entry.references.length })}</small>
                    ) : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p>{t("agent.harness.noActivity")}</p>
            )}
          </section>
        ) : null}
      </div>
    </details>
  );
}

function BadgeLike({ available }: { available: boolean }) {
  const { t } = useI18n();
  return (
    <span className={available ? styles.evidenceAvailable : styles.evidenceMissing}>
      {available ? t("common.available") : t("common.unavailable")}
    </span>
  );
}
