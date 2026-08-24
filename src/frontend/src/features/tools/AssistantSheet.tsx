import type { FormEvent } from "react";

import type { ChatMessage } from "../../lib/types/assistant";
import type { WorkspaceSelectionContext } from "../../lib/workspaceSelection";
import { Sheet } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import { AssistantPanel } from "./AssistantPanel";
import styles from "./AssistantSheet.module.css";

export interface AssistantSheetProps {
  activePageLabel: string;
  error: string;
  input: string;
  loading: boolean;
  messages: ChatMessage[];
  open: boolean;
  projectName: string;
  selectionContext: WorkspaceSelectionContext;
  onInput: (value: string) => void;
  onNewChat: () => void;
  onOpenChange: (open: boolean) => void;
  onSubmit: (event: FormEvent) => void;
}

type PromptSuggestion = {
  kind: string;
  text: string;
};

export function AssistantSheet({
  activePageLabel,
  error,
  input,
  loading,
  messages,
  onInput,
  onNewChat,
  onOpenChange,
  onSubmit,
  open,
  projectName,
  selectionContext,
}: AssistantSheetProps) {
  const { t } = useI18n();
  const suggestedPrompts = getSuggestedPrompts(activePageLabel, t);
  const selectedObjectText = [
    selectionContext.dataSeries
      ? t("assistant.selection.dataSeries", {
          subject: selectionContext.dataSeries.subject,
          series: selectionContext.dataSeries.series,
        })
      : "",
    selectionContext.image.subjectId
      ? t("assistant.selection.subject", { subject: selectionContext.image.subjectId })
      : "",
    selectionContext.image.series
      ? t("assistant.selection.series", { series: selectionContext.image.series })
      : "",
    selectionContext.planNode
      ? t("assistant.selection.node", { node: selectionContext.planNode.name })
      : "",
    selectionContext.artifact
      ? t("assistant.selection.artifact", { artifact: selectionContext.artifact.name })
      : "",
  ]
    .filter(Boolean)
    .join(" / ");

  return (
    <Sheet
      closeLabel={t("assistant.close")}
      description={t("assistant.description")}
      onOpenChange={onOpenChange}
      open={open}
      title={t("nav.assistant")}
    >
      <div className={styles.sheetBody}>
        <section className={styles.contextPanel} aria-label={t("assistant.context")}>
          <div>
            <span>{t("assistant.project")}</span>
            <strong>{projectName || t("assistant.noProject")}</strong>
          </div>
          <div>
            <span>{t("assistant.workspace")}</span>
            <strong>{activePageLabel}</strong>
          </div>
          <div>
            <span>{t("assistant.run")}</span>
            <strong>{formatRun(selectionContext, t)}</strong>
          </div>
          <div>
            <span>{t("assistant.selection")}</span>
            <strong>{selectedObjectText || t("assistant.noSelection")}</strong>
          </div>
          <div>
            <span>{t("assistant.actionMode")}</span>
            <strong>{t("assistant.actionModeValue")}</strong>
          </div>
          <div>
            <span>{t("assistant.provider")}</span>
            <strong>{t("assistant.providerValue")}</strong>
          </div>
        </section>

        <section className={styles.suggestionPanel} aria-label={t("assistant.suggestions")}>
          <div className={styles.panelHeader}>
            <h3>{t("assistant.suggestedPrompts")}</h3>
            <p>{t("assistant.promptBoundary")}</p>
          </div>
          <div className={styles.promptGrid}>
            {suggestedPrompts.map((prompt) => (
              <button
                aria-label={prompt.text}
                key={prompt.text}
                type="button"
                onClick={() => onInput(prompt.text)}
              >
                <span>{prompt.kind}</span>
                <strong>{prompt.text}</strong>
              </button>
            ))}
          </div>
        </section>

        <div className={styles.actionBoundary}>
          <strong>{t("assistant.executionBoundary")}</strong>
          <p>{t("assistant.executionDescription")}</p>
        </div>

        <AssistantPanel
          error={error}
          input={input}
          loading={loading}
          messages={messages}
          onInput={onInput}
          onNewChat={onNewChat}
          onSubmit={onSubmit}
        />
      </div>
    </Sheet>
  );
}

function formatRun(
  selectionContext: WorkspaceSelectionContext,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (!selectionContext.run.id) return t("assistant.noRun");
  return selectionContext.run.name ?? selectionContext.run.id;
}

function getSuggestedPrompts(
  activePageLabel: string,
  t: ReturnType<typeof useI18n>["t"],
): PromptSuggestion[] {
  const kinds = {
    explanation: t("assistant.kind.explanation"),
    summary: t("assistant.kind.summary"),
    draft: t("assistant.kind.draft"),
  };
  const promptSets = {
    default: [
      { kind: kinds.explanation, text: t("assistant.prompt.defaultExplain") },
      { kind: kinds.summary, text: t("assistant.prompt.defaultSummary") },
      { kind: kinds.draft, text: t("assistant.prompt.defaultDraft") },
    ],
    agent: [
      { kind: kinds.explanation, text: t("assistant.prompt.agentExplain") },
      { kind: kinds.summary, text: t("assistant.prompt.agentSummary") },
      { kind: kinds.draft, text: t("assistant.prompt.agentDraft") },
    ],
    settings: [
      { kind: kinds.explanation, text: t("assistant.prompt.settingsExplain") },
      { kind: kinds.summary, text: t("assistant.prompt.settingsSummary") },
      { kind: kinds.draft, text: t("assistant.prompt.settingsDraft") },
    ],
    runs: [
      { kind: kinds.explanation, text: t("assistant.prompt.runsExplain") },
      { kind: kinds.summary, text: t("assistant.prompt.runsSummary") },
      { kind: kinds.draft, text: t("assistant.prompt.runsDraft") },
    ],
  };

  if (activePageLabel === t("nav.agent") || activePageLabel === "Agent") return promptSets.agent;
  if (
    activePageLabel === t("nav.settings") ||
    activePageLabel === "Settings / Environment" ||
    activePageLabel === "Settings"
  ) {
    return promptSets.settings;
  }
  if (activePageLabel === t("nav.runs") || activePageLabel === "Runs") return promptSets.runs;
  return promptSets.default;
}
