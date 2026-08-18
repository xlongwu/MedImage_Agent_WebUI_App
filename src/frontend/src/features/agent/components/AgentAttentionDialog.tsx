import { useEffect, useMemo, useState } from "react";

import { Dialog } from "../../../components/ui";
import { useI18n } from "../../../i18n/useI18n";
import { attentionActionFor, type AttentionAction } from "../attentionAction";
import type { AgentTaskController } from "../useAgentTaskController";
import { DecisionBatchForm } from "./DecisionBatchForm";
import { ExecutionApprovalReview } from "./ExecutionApprovalReview";
import { RecoveryApprovalReview } from "./RecoveryApprovalReview";

export type AgentAttentionDialogHandle = {
  hasAttention: boolean;
  reopen: () => void;
};

export function useAgentAttentionDialog(
  controller: AgentTaskController,
  projectId: string | null,
): AgentAttentionDialogHandle & {
  action: AttentionAction | null;
  open: boolean;
  dismiss: () => void;
} {
  const action = useMemo(
    () => (controller.task?.project_id === projectId ? attentionActionFor(controller.task) : null),
    [controller.task, projectId],
  );
  const [dismissedKey, setDismissedKey] = useState<string | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);

  useEffect(() => {
    setDismissedKey(null);
    setOpenKey(action?.key ?? null);
  }, [action?.key, projectId]);

  const isOpen = action !== null && dismissedKey !== action.key && openKey === action.key;
  return {
    action,
    dismiss: () => {
      if (!action) return;
      setDismissedKey(action.key);
      setOpenKey(null);
    },
    hasAttention: action !== null,
    open: isOpen,
    reopen: () => {
      if (action) {
        setDismissedKey(null);
        setOpenKey(action.key);
      }
    },
  };
}

export function AgentAttentionDialog({
  attention,
  controller,
}: {
  attention: ReturnType<typeof useAgentAttentionDialog>;
  controller: AgentTaskController;
}) {
  const { t } = useI18n();
  const task = controller.task;
  const action = attention.action;
  if (!task || !action) return null;

  const title =
    action.type === "decision"
      ? t("agent.confirmation.decision.title")
      : action.type === "approval"
        ? t("agent.confirmation.execution.title")
        : t("agent.confirmation.recovery.title");
  const description =
    action.type === "decision"
      ? t("agent.confirmation.decision.description")
      : action.type === "approval"
        ? t("agent.confirmation.execution.description")
        : t("agent.confirmation.recovery.description");

  return (
    <Dialog
      closeLabel={t("common.dismiss")}
      description={description}
      key={action.key}
      onOpenChange={(open) => {
        if (!open) attention.dismiss();
      }}
      open={attention.open}
      title={title}
    >
      {action.type === "decision" && task.decision_batch ? (
        <DecisionBatchForm
          batch={task.decision_batch}
          errorDetails={controller.errorDetails}
          mutating={controller.mutating}
          onAnswer={controller.answer}
        />
      ) : null}
      {action.type === "approval" ? (
        <ExecutionApprovalReview
          mutating={controller.mutating}
          onApprove={controller.approve}
          task={task}
        />
      ) : null}
      {action.type === "recovery" && task.recovery ? (
        <RecoveryApprovalReview
          mutating={controller.mutating}
          onApprove={controller.approveRecovery}
          recovery={task.recovery}
        />
      ) : null}
    </Dialog>
  );
}
