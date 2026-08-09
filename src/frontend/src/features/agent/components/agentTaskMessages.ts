import type { MessageKey } from "../../../i18n/messages/en";

const RESULT_MESSAGE_KEYS: Record<string, MessageKey> = {
  "Research goal satisfied": "agent.result.satisfied.title",
  "Research goal not fully satisfied": "agent.result.notSatisfied.title",
  "Result needs attention": "agent.result.needsAttention.title",
  "The goal is supported by complete, registered, reloadable numerical evidence.":
    "agent.result.satisfied.summary",
  "Some reviewed evidence failed or remained incomplete.": "agent.result.notSatisfied.summary",
  "Evidence is incomplete, conflicting, or not reloadable.": "agent.result.needsAttention.summary",
  "Only part of the reviewed subject or artifact scope completed.":
    "agent.result.limitation.partial",
  "This is a preview result and is not a full-dataset result.":
    "agent.result.limitation.previewOnly",
  "A scientifically simplified method was used; review its limitations.":
    "agent.result.limitation.simplified",
  "Only metadata evidence exists; no declared numerical result was computed.":
    "agent.result.limitation.metadataOnly",
};

export function getAgentResultMessageKey(value: string): MessageKey | undefined {
  return RESULT_MESSAGE_KEYS[value];
}

export function getAgentApprovalMessage(value: string): { key: MessageKey; count: string } | null {
  const subjects = /^(\d+) registered subject\(s\)$/.exec(value);
  if (subjects) return { key: "agent.approvalDatasetSubjects", count: subjects[1] };
  const nodes = /^(\d+) reviewed node\(s\); no dispatch before approval$/.exec(value);
  if (nodes) return { key: "agent.approvalReviewedNodes", count: nodes[1] };
  return null;
}
