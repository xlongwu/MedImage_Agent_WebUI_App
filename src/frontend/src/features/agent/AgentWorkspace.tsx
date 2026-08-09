import { useState } from "react";

import { Button, Card, Dialog, EmptyState, Skeleton } from "../../components/ui";
import type { I18nContextValue } from "../../i18n/context";
import { useI18n } from "../../i18n/useI18n";
import type { ProjectInventory } from "../../lib/projectWorkflow";
import type { LegacyWorkspace } from "../navigation/workspaceModel";
import styles from "./AgentWorkspace.module.css";
import { CurrentAction } from "./components/CurrentAction";
import { DecisionBatchCard } from "./components/DecisionBatchCard";
import { HarnessStatusCard } from "./components/HarnessStatusCard";
import { GoalComposer } from "./components/GoalComposer";
import { MacroProgress } from "./components/MacroProgress";
import { isDecisionAction, NextActionCard } from "./components/NextActionCard";
import { ProjectSummaryCard } from "./components/ProjectSummaryCard";
import { ResultSummaryCard } from "./components/ResultSummaryCard";
import { RecoveryActionCard } from "./components/RecoveryActionCard";
import { TaskDetails } from "./components/TaskDetails";
import type { AgentTaskController } from "./useAgentTaskController";

type LocalizedAgentError = {
  message: string;
  retryLabel: string | null;
  title: string;
};

function localizeAgentError(
  error: string,
  code: string | null | undefined,
  t: I18nContextValue["t"],
): LocalizedAgentError {
  if (code === "AGENT_DECISION_STALE" || code === "AGENT_DECISION_PLAN_STALE") {
    return {
      message: t("agent.error.decisionStale"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  if (code === "AGENT_DECISION_BATCH_EXPIRED") {
    return {
      message: t("agent.error.decisionExpired"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  if (code === "AGENT_DECISION_BATCH_INVALID") {
    return {
      message: t("agent.error.decisionInvalid"),
      retryLabel: null,
      title: t("agent.actionProblem"),
    };
  }
  if (code === "APPROVAL_SUMMARY_STALE" || code === "APPROVAL_SUMMARY_EXPIRED") {
    return {
      message: t("agent.error.approvalStale"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  if (code?.includes("BUDGET_EXHAUSTED")) {
    return {
      message: t("agent.error.budgetExhausted"),
      retryLabel: null,
      title: t("agent.actionProblem"),
    };
  }
  if (code === "AGENT_HARNESS_CONTEXT_LIMIT_EXCEEDED") {
    return {
      message: t("agent.error.contextLimit"),
      retryLabel: null,
      title: t("agent.actionProblem"),
    };
  }
  if (code?.includes("CAPABILITY_DENIED")) {
    return {
      message: t("agent.error.capabilityDenied"),
      retryLabel: null,
      title: t("agent.actionProblem"),
    };
  }
  if (code === "RECOVERY_APPROVAL_STALE") {
    return {
      message: t("agent.error.recoveryStale"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  if (code === "HUMAN_HANDOFF_REQUIRED") {
    return { message: t("agent.error.handoff"), retryLabel: null, title: t("agent.actionProblem") };
  }
  if (error.includes("REVIEWED_EXECUTION_DISABLED")) {
    return {
      message: t("agent.error.reviewedExecutionDisabled.message"),
      retryLabel: t("agent.error.checkAgain"),
      title: t("agent.error.reviewedExecutionDisabled.title"),
    };
  }
  if (error.includes("APPROVAL_SUMMARY_EXPIRED")) {
    return {
      message: t("agent.error.approvalSummaryExpired.message"),
      retryLabel: null,
      title: t("agent.error.approvalSummaryExpired.title"),
    };
  }
  if (error.includes("AGENT_EXECUTION_PREREQUISITE_MISSING")) {
    return {
      message: t("agent.error.executionPrerequisiteMissing.message"),
      retryLabel: null,
      title: t("agent.error.executionPrerequisiteMissing.title"),
    };
  }
  if (error.includes("AGENT_DRY_RUN_BLOCKED")) {
    return {
      message: t("agent.error.dryRunBlocked.message"),
      retryLabel: null,
      title: t("agent.error.dryRunBlocked.title"),
    };
  }
  if (error.includes("EXECUTION_TICKET_EXPIRED")) {
    return {
      message: t("agent.error.ticketExpired.message"),
      retryLabel: null,
      title: t("agent.error.ticketExpired.title"),
    };
  }
  if (error.includes("GATEWAY_DISPATCH_OUTCOME_UNKNOWN")) {
    return {
      message: t("agent.error.dispatchUnknown.message"),
      retryLabel: null,
      title: t("agent.error.dispatchUnknown.title"),
    };
  }
  if (error.includes("EXECUTION_DISPATCH_FAILED") || error.includes("EXECUTION_FAILED")) {
    return {
      message: t("agent.error.executionFailed.message"),
      retryLabel: null,
      title: t("agent.error.executionFailed.title"),
    };
  }
  if (
    error.includes("MEMORY_STORE_UNHEALTHY") ||
    error.includes("MEMORY_OUTBOX_PREFLIGHT_FAILED")
  ) {
    return {
      message: t("agent.error.memoryUnavailable.message"),
      retryLabel: t("agent.error.checkAgain"),
      title: t("agent.error.memoryUnavailable.title"),
    };
  }
  if (error.includes("LIFECYCLE_CANCEL_NOT_SUPPORTED")) {
    return {
      message: t("agent.error.alreadyTerminal"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  if (error.includes("UNSUPPORTED_GOAL") || error.includes("GOAL_KIND_UNSUPPORTED_OR_AMBIGUOUS"))
    return {
      message: t("agent.error.goalUnsupported"),
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  if (error.includes("AGENT_EXECUTION_BLOCKED")) {
    return {
      message: error,
      retryLabel: t("common.retry"),
      title: t("agent.actionProblem"),
    };
  }
  return {
    message: error,
    retryLabel: t("common.retry"),
    title: t("agent.connectionProblem"),
  };
}

export function AgentWorkspace({
  advancedMode,
  controller,
  inventory,
  onOpenLegacyWorkspace,
  onOpenReviewedPlan,
  onOpenRuns,
  projectName,
}: {
  advancedMode: boolean;
  controller: AgentTaskController;
  inventory: ProjectInventory | null;
  onOpenLegacyWorkspace: (workspace: LegacyWorkspace) => void;
  onOpenReviewedPlan?: (reviewedPlanId: string) => void;
  onOpenRuns: () => void;
  projectName: string;
}) {
  return (
    <AgentWorkspaceView
      advancedMode={advancedMode}
      controller={controller}
      dataStateLabel={inventory?.dataStateLabel ?? "—"}
      onOpenLegacyWorkspace={onOpenLegacyWorkspace}
      onOpenReviewedPlan={onOpenReviewedPlan}
      onOpenRuns={onOpenRuns}
      projectName={projectName}
    />
  );
}

export function AgentWorkspaceView({
  advancedMode,
  controller,
  dataStateLabel,
  onOpenLegacyWorkspace,
  onOpenReviewedPlan,
  onOpenRuns,
  projectName,
}: {
  advancedMode: boolean;
  controller: AgentTaskController;
  dataStateLabel: string;
  onOpenLegacyWorkspace: (workspace: LegacyWorkspace) => void;
  onOpenReviewedPlan?: (reviewedPlanId: string) => void;
  onOpenRuns: () => void;
  projectName: string;
}) {
  const { t } = useI18n();
  const task = controller.task;
  const [confirmNewTask, setConfirmNewTask] = useState(false);
  const localizedError = controller.error
    ? localizeAgentError(controller.error, controller.errorCode, t)
    : null;
  const planOnlyResult = Boolean(
    task?.result_summary?.artifacts.some((artifact) => artifact.artifact_type === "reviewed_plan"),
  );

  return (
    <div className={styles.workspace}>
      <header className={styles.hero}>
        <div>
          <span className={styles.heroKicker}>{t("agent.researchWorkspace")}</span>
          <h1>{t("agent.title")}</h1>
          <p>{t("agent.subtitle")}</p>
        </div>
        {task ? (
          <Button
            disabled={controller.mutating}
            onClick={() => setConfirmNewTask(true)}
            variant="secondary"
          >
            {t("agent.newTask")}
          </Button>
        ) : null}
      </header>

      <ProjectSummaryCard dataStateLabel={dataStateLabel} projectName={projectName} task={task} />

      <Dialog
        description={t("agent.newTaskConfirmation")}
        footer={
          <>
            <Button onClick={() => setConfirmNewTask(false)} variant="ghost">
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => {
                setConfirmNewTask(false);
                controller.dismissTask();
              }}
              variant="danger"
            >
              {t("agent.startNewGoal")}
            </Button>
          </>
        }
        onOpenChange={setConfirmNewTask}
        open={confirmNewTask}
        title={t("agent.newTask")}
      />

      {localizedError ? (
        <Card className={styles.connectionError} role="alert">
          <div>
            <strong>{localizedError.title}</strong>
            <p>{localizedError.message}</p>
          </div>
          {localizedError.retryLabel ? (
            <Button onClick={() => void controller.refresh()} variant="secondary">
              {localizedError.retryLabel}
            </Button>
          ) : null}
        </Card>
      ) : null}

      {controller.loading && !task ? (
        <section className={styles.loadingState} aria-label={t("agent.loading")} role="status">
          <Skeleton height={120} />
          <Skeleton height={220} />
        </section>
      ) : !task ? (
        <GoalComposer disabled={controller.mutating} onSubmit={controller.create} />
      ) : (
        <>
          <CurrentAction
            nextActionType={task.next_action.type}
            outcome={task.outcome}
            progress={task.progress}
            state={task.state}
          />
          {task.harness_summary ? <HarnessStatusCard summary={task.harness_summary} /> : null}
          <MacroProgress
            outcome={task.outcome}
            planOnly={planOnlyResult}
            progress={task.progress}
          />
          {task.recovery && task.next_action.type === "approve_recovery" ? (
            <RecoveryActionCard
              mutating={controller.mutating}
              onAbandon={() => controller.cancel(t("agent.recovery.abandonReason"))}
              onApprove={controller.approveRecovery}
              onOpenDetails={onOpenRuns}
              recovery={task.recovery}
            />
          ) : task.state === "completed" && planOnlyResult ? null : isDecisionAction(task) ? (
            <DecisionBatchCard
              batch={task.decision_batch!}
              errorDetails={controller.errorDetails}
              mutating={controller.mutating}
              onAnswer={controller.answer}
            />
          ) : (
            <NextActionCard
              key={task.next_action.decision_batch_id ?? task.next_action.type}
              mutating={controller.mutating}
              onApprove={controller.approve}
              onCancel={controller.cancel}
              onOpenRuns={onOpenRuns}
              task={task}
            />
          )}
          {task.result_summary ? (
            <ResultSummaryCard
              onOpenRuns={onOpenRuns}
              result={task.result_summary}
              explanation={task.result_explanation}
            />
          ) : null}
          {task.state === "completed" && task.outcome !== "canceled" && !task.result_summary ? (
            <EmptyState
              title={t("agent.resultUnavailable")}
              description={t("agent.resultUnavailableDescription")}
            />
          ) : null}
          <TaskDetails
            advancedMode={advancedMode}
            harnessActivity={controller.harnessActivity}
            onLoadHarnessActivity={controller.loadHarnessActivity}
            onOpenLegacyWorkspace={onOpenLegacyWorkspace}
            onOpenReviewedPlan={onOpenReviewedPlan ?? (() => onOpenLegacyWorkspace("plan"))}
            onOpenRuns={onOpenRuns}
            task={task}
          />
        </>
      )}
    </div>
  );
}
