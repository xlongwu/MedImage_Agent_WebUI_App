import { useState } from "react";

import { Badge, Button, Card } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import type { AgentTaskResponse } from "../../../lib/types/agentTask";
import styles from "../AgentWorkspace.module.css";

export function NextActionCard({
  mutating,
  onAnswer,
  onApprove,
  onCancel,
  onOpenRuns,
  task,
}: {
  mutating: boolean;
  onAnswer: (batchId: string, answers: { item_id: string; value: string }[]) => Promise<void>;
  onApprove: () => Promise<void>;
  onCancel: (reason?: string) => Promise<void>;
  onOpenRuns: () => void;
  task: AgentTaskResponse;
}) {
  const { t } = useI18n();
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries(task.decisions.map((item) => [item.item_id, item.recommended_option ?? ""])),
  );

  const type = task.next_action.type;
  const needsAnswer =
    type === "answer_science_decision" || type === "provide_input" || type === "revise_goal";
  const isApproval = type === "approve_execution" || type === "approve_recovery";
  const showPrimary =
    needsAnswer || isApproval || type === "review_results" || type === "view_attention";
  const title =
    task.outcome === "canceled"
      ? t("agent.next.canceled.title")
      : type === "revise_goal"
        ? t("agent.next.reviseGoal.title")
        : type === "provide_input"
          ? t("agent.next.provideInput.title")
          : type === "answer_science_decision"
            ? t("agent.next.scienceDecision.title")
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
    type === "revise_goal"
      ? t("agent.next.reviseGoal.description")
      : type === "provide_input"
        ? t("agent.next.provideInput.description")
        : type === "approve_execution"
          ? t("agent.next.approveExecution.description")
          : task.next_action.description;
  const decisionQuestion = (decision: (typeof task.decisions)[number]) =>
    decision.kind === "goal_revision"
      ? t("agent.decision.goalRevision.question")
        : decision.kind === "missing_input"
        ? t("agent.decision.missingInput.question")
        : decision.question;
  const decisionImpact = (decision: (typeof task.decisions)[number]) =>
    decision.kind === "goal_revision"
      ? t("agent.decision.goalRevision.impact")
        : decision.kind === "missing_input"
        ? t("agent.decision.missingInput.impact")
        : decision.impact;
  const datasetCountMatch = task.approval_summary?.dataset_summary.match(
    /^(\d+) registered subject\(s\)$/,
  );
  const executionCountMatch = task.approval_summary?.execution_summary.match(
    /^(\d+) reviewed node\(s\); no dispatch before approval$/,
  );
  const approvalDatasetSummary = datasetCountMatch
    ? t("agent.approvalDatasetSubjects", { count: datasetCountMatch[1] })
    : task.approval_summary?.dataset_summary;
  const approvalExecutionSummary = executionCountMatch
    ? t("agent.approvalReviewedNodes", { count: executionCountMatch[1] })
    : task.approval_summary?.execution_summary;

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

      {task.decisions.length && needsAnswer ? task.decisions.map((decision) => (
        <fieldset className={styles.decisionOptions} key={decision.item_id}>
          <legend>{decisionQuestion(decision)}</legend>
          {decision.source === "memory_suggestion" ? (
            <Badge tone="info">{t("agent.decision.memorySuggestion")}</Badge>
          ) : null}
          <p>{decisionImpact(decision)}</p>
          {decision.options.length ? (
            decision.options.map((option) => (
              <label key={option.id}>
                <input
                  checked={answers[decision.item_id] === option.id}
                  name={decision.item_id}
                  onChange={() => setAnswers((current) => ({ ...current, [decision.item_id]: option.id }))}
                  type="radio"
                  value={option.id}
                />
                <span>
                  <strong>{option.label}</strong>
                  <small>{option.description}</small>
                </span>
                {option.recommended ? (
                  <Badge size="sm" tone="info">
                    {t("agent.recommended")}
                  </Badge>
                ) : null}
              </label>
            ))
          ) : (
            <textarea
              aria-label={
                type === "revise_goal" ? t("agent.goalRevisionLabel") : t("agent.answerLabel")
              }
              onChange={(event) => setAnswers((current) => ({ ...current, [decision.item_id]: event.target.value }))}
              rows={3}
              value={answers[decision.item_id] ?? ""}
            />
          )}
        </fieldset>
      )) : null}

      {task.approval_summary && isApproval ? (
        <div className={styles.approvalSummary}>
          <div>
            <span>{t("agent.approvalGoal")}</span>
            <strong>{task.approval_summary.goal}</strong>
          </div>
          <div>
            <span>{t("agent.approvalData")}</span>
            <strong>{approvalDatasetSummary}</strong>
          </div>
          <div>
            <span>{t("agent.approvalExecution")}</span>
            <strong>{approvalExecutionSummary}</strong>
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
              disabled={
                mutating ||
                Boolean(task.next_action.disabled_reason) ||
                (needsAnswer && task.decisions.some((item) => item.required && !(answers[item.item_id] ?? "").trim()))
              }
              onClick={() => {
                if (needsAnswer && task.decision_batch)
                  void onAnswer(task.decision_batch.batch_id, task.decisions.map((item) => ({ item_id: item.item_id, value: answers[item.item_id] ?? "" }))).catch((): void => {});
                else if (isApproval) void onApprove().catch((): void => {});
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
                  : needsAnswer
                    ? type === "revise_goal"
                      ? t("agent.updateGoal")
                      : t("agent.continue")
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
