import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";
import { getAgentApprovalMessage } from "./agentTaskMessages";

export function isDecisionAction(task: AgentTaskResponse): boolean {
  return (
    task.decision_batch !== null &&
    (task.next_action.type === "answer_science_decision" ||
      task.next_action.type === "provide_input" ||
      task.next_action.type === "revise_goal")
  );
}

export function NextActionCard({
  mutating,
  onApprove,
  onCancel,
  onOpenRuns,
  task,
}: {
  mutating: boolean;
  onApprove: () => Promise<void>;
  onCancel: (reason?: string) => Promise<void>;
  onOpenRuns: () => void;
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
      : task.next_action.description;
  const localizeApprovalSummary = (value: string) => {
    const parsed = getAgentApprovalMessage(value);
    return parsed ? t(parsed.key, { count: parsed.count }) : value;
  };

  return (
    <Card className={styles.nextAction} tone="elevated">
      <div className={styles.nextActionHeader}>
        <div>
          <span className={styles.stepNumber}>03</span>
          <span className={styles.eyebrow}>{t("agent.nextAction")}</span>
          <h2 tabIndex={-1}>{title}</h2>
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
            <strong>{localizeApprovalSummary(task.approval_summary.dataset_summary)}</strong>
          </div>
          <div>
            <span>{t("agent.approvalExecution")}</span>
            <strong>{localizeApprovalSummary(task.approval_summary.execution_summary)}</strong>
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
        <span>{task.next_action.disabled_reason}</span>
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
              data-primary-action="true"
              disabled={mutating || Boolean(task.next_action.disabled_reason)}
              onClick={() => {
                if (isApproval) void onApprove().catch((): void => {});
                else onOpenRuns();
              }}
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
    </Card>
  );
}
