import { Badge, Button } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function TaskActionPanel({
  mutating,
  onCancel,
  onOpenRuns,
  onReopenAttention,
  task,
}: {
  mutating: boolean;
  onCancel: (reason?: string) => Promise<void>;
  onOpenRuns: () => void;
  onReopenAttention: () => void;
  task: AgentTaskResponse;
}) {
  const { t } = useI18n();
  const type = task.next_action.type;
  const isApproval = type === "approve_execution" || type === "approve_recovery";
  const showPrimary = isApproval || type === "review_results" || type === "view_attention";
  const title =
    task.outcome === "canceled"
      ? t("agent.next.canceled.title")
      : type === "approve_execution"
        ? t("agent.next.approveExecution.title")
        : type === "approve_recovery"
          ? t("agent.next.approveRecovery.title")
          : type === "review_results"
            ? t("agent.next.reviewResults.title")
            : type === "view_attention"
              ? t("agent.next.viewAttention.title")
              : t("agent.next.none.title");
  const description =
    type === "approve_execution"
      ? t("agent.next.approveExecution.description")
      : type === "provide_input"
        ? t("agent.next.provideInput.description")
        : type === "view_attention"
          ? t("agent.next.viewAttention.description")
          : null;

  return (
    <section className={styles.taskAction}>
      <div className={styles.taskPanelHeader}>
        <div>
          <span className={styles.eyebrow}>{t("agent.nextAction")}</span>
          <h3 tabIndex={-1}>{title}</h3>
          {description ? <p>{description}</p> : null}
        </div>
        {task.next_action.requires_user ? (
          <Badge tone="warning">{t("agent.waitingForYou")}</Badge>
        ) : (
          <Badge tone="info">{t("agent.automatic")}</Badge>
        )}
      </div>

      {task.approval_summary && isApproval ? (
        <div className={styles.approvalSummary}>
          <div>
            <span>{t("agent.approvalGoal")}</span>
            <strong>{task.approval_summary.goal}</strong>
          </div>
          <div>
            <span>{t("agent.approvalData")}</span>
            <strong>
              {task.approval_summary.selected_subject_ids.length
                ? t("agent.approvalSelectedSubjects", {
                    selected: task.approval_summary.selected_subject_ids.length,
                    registered: task.approval_summary.registered_subject_count,
                  })
                : t("agent.approvalRegisteredSubjects", {
                    count: task.approval_summary.registered_subject_count,
                  })}
            </strong>
          </div>
          <div>
            <span>{t("agent.approvalExecution")}</span>
            <strong>
              {t("agent.approvalReviewedNodes", { count: task.approval_summary.node_ids.length })}
            </strong>
          </div>
          <div>
            <span>{t("agent.approvalWrites")}</span>
            <strong>{task.approval_summary.write_roots.join(" · ")}</strong>
          </div>
          <div>
            <span>{t("agent.approvalSafety")}</span>
            <strong>
              {task.approval_summary.rawdata_read_only
                ? t("agent.rawdataReadonly")
                : t("agent.safetyUnavailable")}
            </strong>
          </div>
          {task.approval_summary.revision_no ? (
            <div>
              <span>{t("agent.approvalPlanRevision")}</span>
              <strong>{task.approval_summary.revision_no}</strong>
            </div>
          ) : null}
          {task.approval_summary.science_changes.length ? (
            <div>
              <span>{t("agent.scienceChanges")}</span>
              <strong>{task.approval_summary.science_changes.join(" · ")}</strong>
            </div>
          ) : null}
          {(task.approval_summary.memory_influence_summary ?? []).length ? (
            <div>
              <span>{t("agent.approvalMemory")}</span>
              <strong>{task.approval_summary.memory_influence_summary?.join(" · ")}</strong>
            </div>
          ) : null}
          {task.approval_summary.limitations.length ? (
            <div>
              <span>{t("agent.limitations")}</span>
              <strong>{task.approval_summary.limitations.join(" · ")}</strong>
            </div>
          ) : null}
          {task.approval_summary.sections.map((section) => (
            <div key={section.id}>
              <span>{section.title}</span>
              <strong>{section.summary}</strong>
              {section.warnings.length ? <small>{section.warnings.join(" · ")}</small> : null}
            </div>
          ))}
        </div>
      ) : null}

      <div className={styles.actionFooter}>
        <div>
          {task.state !== "running" && task.state !== "completed" && task.outcome !== "canceled" ? (
            <Button
              disabled={mutating}
              onClick={() => void onCancel(t("agent.cancelReason")).catch((): void => {})}
              variant="ghost"
            >
              {t("agent.cancelTask")}
            </Button>
          ) : null}
          {showPrimary ? (
            <Button
              data-agent-action={isApproval ? "reopen_approve_execution" : undefined}
              data-primary-action="true"
              disabled={mutating}
              onClick={isApproval ? onReopenAttention : onOpenRuns}
              variant="primary"
            >
              {mutating
                ? t("agent.working")
                : isApproval
                  ? type === "approve_recovery"
                    ? t("agent.approveRecovery")
                    : t("agent.approvePlan")
                  : type === "review_results"
                    ? t("agent.viewResults")
                    : t("agent.viewDetails")}
            </Button>
          ) : null}
        </div>
      </div>
    </section>
  );
}
