import { Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function ExecutionApprovalReview({
  mutating,
  onApprove,
  task,
}: {
  mutating: boolean;
  onApprove: () => Promise<void>;
  task: AgentTaskResponse;
}) {
  const { t } = useI18n();
  const summary = task.approval_summary;
  if (!summary) return null;

  return (
    <div className={styles.attentionReview}>
      <div className={styles.approvalSummary}>
        <div>
          <span>{t("agent.approvalGoal")}</span>
          <strong>{summary.goal}</strong>
        </div>
        <div>
          <span>{t("agent.approvalData")}</span>
          <strong>
            {summary.selected_subject_ids.length
              ? t("agent.approvalSelectedSubjects", {
                  selected: summary.selected_subject_ids.length,
                  registered: summary.registered_subject_count,
                })
              : t("agent.approvalRegisteredSubjects", {
                  count: summary.registered_subject_count,
                })}
          </strong>
        </div>
        <div>
          <span>{t("agent.approvalExecution")}</span>
          <strong>{t("agent.approvalReviewedNodes", { count: summary.node_ids.length })}</strong>
        </div>
        <div>
          <span>{t("agent.approvalWrites")}</span>
          <strong>{summary.write_roots.join(" · ")}</strong>
        </div>
        <div>
          <span>{t("agent.approvalSafety")}</span>
          <strong>
            {summary.rawdata_read_only ? t("agent.rawdataReadonly") : t("agent.safetyUnavailable")}
          </strong>
        </div>
        {task.technical_details?.backend?.selected ? (
          <div>
            <span>{t("agent.approvalBackend")}</span>
            <strong>{task.technical_details.backend.selected}</strong>
          </div>
        ) : null}
        {task.technical_details?.node_ids.length ? (
          <div>
            <span>{t("agent.approvalNodes")}</span>
            <strong>{task.technical_details.node_ids.join(" · ")}</strong>
          </div>
        ) : null}
        {summary.science_changes.length ? (
          <div>
            <span>{t("agent.scienceChanges")}</span>
            <strong>{summary.science_changes.join(" · ")}</strong>
          </div>
        ) : null}
        {(summary.memory_influence_summary ?? []).length ? (
          <div>
            <span>{t("agent.approvalMemory")}</span>
            <strong>{summary.memory_influence_summary?.join(" · ")}</strong>
          </div>
        ) : null}
        {summary.limitations.length ? (
          <div>
            <span>{t("agent.limitations")}</span>
            <strong>{summary.limitations.join(" · ")}</strong>
          </div>
        ) : null}
        {summary.sections.map((section) => (
          <div key={section.id}>
            <span>{section.title}</span>
            <strong>{section.summary}</strong>
            {section.warnings.length ? <small>{section.warnings.join(" · ")}</small> : null}
          </div>
        ))}
      </div>
      <div className={styles.attentionAction}>
        <Button
          data-agent-action="approve_execution"
          data-primary-action="true"
          disabled={mutating}
          onClick={() => void onApprove().catch((): void => {})}
          variant="primary"
        >
          {mutating ? t("agent.working") : t("agent.confirmation.execution.confirm")}
        </Button>
      </div>
    </div>
  );
}
