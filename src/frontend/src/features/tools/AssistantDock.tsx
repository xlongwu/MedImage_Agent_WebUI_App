import type { FormEvent } from "react";

import { Icon } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import type { ChatMessage } from "../../lib/types/assistant";
import { AssistantPanel } from "./AssistantPanel";
import styles from "./AssistantDock.module.css";

export function AssistantDock({
  activePageLabel,
  error,
  input,
  loading,
  messages,
  onClose,
  onInput,
  onNewChat,
  onSubmit,
  projectName,
}: {
  activePageLabel: string;
  error: string;
  input: string;
  loading: boolean;
  messages: ChatMessage[];
  onClose: () => void;
  onInput: (value: string) => void;
  onNewChat: () => void;
  onSubmit: (event: FormEvent) => void;
  projectName: string;
}) {
  const { t } = useI18n();
  return (
    <aside className={styles.dock} aria-label={t("assistant.explainEntry")}>
      <header className={styles.header}>
        <div>
          <span>{activePageLabel}</span>
          <h2>{t("assistant.explainEntry")}</h2>
          <p>{projectName || t("assistant.noProject")}</p>
        </div>
        <button aria-label={t("assistant.close")} onClick={onClose} type="button">
          <Icon height={16} name="x" width={16} />
        </button>
      </header>
      <div className={styles.boundary}>
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
    </aside>
  );
}
