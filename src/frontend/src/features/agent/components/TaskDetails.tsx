import { formatSystemMessage } from "../systemMessages";
import { Button } from "../../../components/ui";
import { useEffect, useRef, useState } from "react";
import { useI18n } from "../../../i18n/useI18n";
import type {
  AgentHarnessActivityPage,
  AgentHarnessSummary,
  AgentTaskResponse,
  AgentPlanEvidence,
} from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import { HarnessSummary } from "./HarnessSummary";
import { TechnicalEvidence } from "./TechnicalEvidence";
import { PlanEvidenceDetails } from "./PlanEvidenceDetails";

type TaskDetailsProps = {
  advancedMode: boolean;
  harnessActivity: AgentHarnessActivityPage | null;
  harnessSummary: AgentHarnessSummary | null | undefined;
  onLoadHarnessActivity: () => Promise<void>;
  onReadPlanEvidence: () => Promise<AgentPlanEvidence>;
  onOpenRuns: () => void;
  task: AgentTaskResponse;
};

export function TaskDetails(props: TaskDetailsProps) {
  const { task } = props;
  return (
    <ScopedTaskDetails
      key={`${task.project_id}:${task.task_id}:${task.technical_details?.plan_hash ?? ""}`}
      {...props}
    />
  );
}

function ScopedTaskDetails({
  advancedMode,
  harnessActivity,
  harnessSummary,
  onLoadHarnessActivity,
  onReadPlanEvidence,
  onOpenRuns,
  task,
}: TaskDetailsProps) {
  const { t } = useI18n();
  const [planEvidence, setPlanEvidence] = useState<AgentPlanEvidence | null>(null);
  const [evidenceState, setEvidenceState] = useState<"idle" | "loading" | "failure">("idle");
  const evidenceRequest = useRef(0);
  useEffect(() => {
    return () => {
      evidenceRequest.current += 1;
    };
  }, []);
  const openPlan = async () => {
    const request = ++evidenceRequest.current;
    setPlanEvidence(null);
    setEvidenceState("loading");
    try {
      const response = await onReadPlanEvidence();
      if (evidenceRequest.current === request) {
        setPlanEvidence(response);
        setEvidenceState("idle");
      }
    } catch {
      if (evidenceRequest.current === request) setEvidenceState("failure");
    }
  };
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
        {harnessSummary ? <HarnessSummary summary={harnessSummary} /> : null}
        <section>
          <h3>{t("agent.evidence")}</h3>
          {task.evidence_links.length ? (
            <ul className={styles.evidenceList}>
              {task.evidence_links.map((link) => (
                <li key={link.id}>
                  <BadgeLike available={link.available} />
                  <span>{formatSystemMessage(t, link.label)}</span>
                  <code>{link.uri}</code>
                  {link.type === "reviewed_plan" ? (
                    <Button
                      disabled={!link.available || evidenceState === "loading"}
                      onClick={() => void openPlan()}
                      variant="secondary"
                    >
                      {t("agent.planEvidence.open")}
                    </Button>
                  ) : null}
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
        {evidenceState === "loading" ? (
          <p role="status">{t("agent.planEvidence.loading")}</p>
        ) : null}
        {evidenceState === "failure" ? (
          <div role="alert">
            <p>{t("agent.planEvidence.failure")}</p>
            <Button onClick={() => void openPlan()}>{t("common.retry")}</Button>
          </div>
        ) : null}
        {planEvidence ? (
          <PlanEvidenceDetails evidence={planEvidence} onClose={() => setPlanEvidence(null)} />
        ) : null}
        {advancedMode && task.technical_details ? (
          <TechnicalEvidence details={task.technical_details} onOpenPlan={() => void openPlan()} />
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
                    <p>
                      {t(`agent.trace.reason.${entry.rationale_code}`)}
                      {entry.decision_kind ? (
                        <>
                          {" "}
                          · <code>{entry.decision_kind}</code>
                        </>
                      ) : null}
                    </p>
                    <p>
                      {t("agent.trace.service")}: <code>{entry.service_name}</code>
                    </p>
                    <p>
                      {t("agent.trace.result")}:{" "}
                      <code>
                        {entry.state_before} → {entry.state_after ?? entry.validation_result}
                      </code>
                    </p>
                    {entry.evidence_facts.length ? (
                      <ul>
                        {entry.evidence_facts.map((fact) => (
                          <li key={fact.key}>
                            <code>{fact.key}</code>: {String(fact.value)}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>{t("agent.noEvidence")}</p>
                    )}
                    {entry.evidence_missing.length ? (
                      <p>
                        {t("agent.trace.missing")}: {entry.evidence_missing.join(" · ")}
                      </p>
                    ) : null}
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
                      <ul>
                        {entry.references.map((ref) => (
                          <li key={`${ref.ref_type}:${ref.ref_id}`}>
                            <code>
                              {ref.ref_type}: {ref.ref_id}
                            </code>{" "}
                            · <code>{ref.content_hash}</code> · <code>{ref.status}</code>
                          </li>
                        ))}
                      </ul>
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
