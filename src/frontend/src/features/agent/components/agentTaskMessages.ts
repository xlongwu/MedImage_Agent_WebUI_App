import type { MessageKey } from "../../../i18n/messages/en";
import type {
  AgentTaskExportDisabledCode,
  AgentTaskRecommendedActionCode,
  AgentTaskResultSummaryCode,
} from "../../../lib/types/agentTask";

export const RESULT_SUMMARY_KEYS: Record<
  AgentTaskResultSummaryCode,
  { title: MessageKey; summary: MessageKey }
> = {
  "result.succeeded": {
    title: "agent.result.satisfied.title",
    summary: "agent.result.satisfied.summary",
  },
  "result.partial": {
    title: "agent.result.notSatisfied.title",
    summary: "agent.result.notSatisfied.summary",
  },
  "result.failed": { title: "agent.result.failed.title", summary: "agent.result.failed.summary" },
  "result.indeterminate": {
    title: "agent.result.needsAttention.title",
    summary: "agent.result.needsAttention.summary",
  },
  "result.plan_only": {
    title: "agent.planOnlyResult.title",
    summary: "agent.planOnlyResult.summary",
  },
};

const LIMITATION_KEYS: Record<string, MessageKey> = {
  partial: "agent.result.limitation.partial",
  preview_only: "agent.result.limitation.previewOnly",
  simplified: "agent.result.limitation.simplified",
  metadata_only: "agent.result.limitation.metadataOnly",
};

const RECOMMENDED_ACTION_KEYS: Record<AgentTaskRecommendedActionCode, MessageKey> = {
  review_technical_evidence: "agent.recommendedAction.reviewTechnicalEvidence",
  review_saved_plan: "agent.recommendedAction.reviewSavedPlan",
};

const EXPORT_DISABLED_KEYS: Record<AgentTaskExportDisabledCode, MessageKey> = {
  no_registered_report: "agent.exportDisabled.noRegisteredReport",
};

export function getAgentLimitationKey(code: string): MessageKey | undefined {
  return Object.prototype.hasOwnProperty.call(LIMITATION_KEYS, code)
    ? LIMITATION_KEYS[code]
    : undefined;
}

export function getAgentRecommendedActionKey(
  code: AgentTaskRecommendedActionCode | null,
): MessageKey | undefined {
  return code ? RECOMMENDED_ACTION_KEYS[code] : undefined;
}

export function getAgentExportDisabledKey(
  code: AgentTaskExportDisabledCode | null | undefined,
): MessageKey | undefined {
  return code ? EXPORT_DISABLED_KEYS[code] : undefined;
}
