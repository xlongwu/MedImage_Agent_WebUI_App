import { memo, useCallback, useState } from "react";

import { Button, Icon, IconButton, Tooltip } from "../../components/ui";
import type { LocalePreference } from "../../hooks/useAppState";
import { useI18n } from "../../i18n/useI18n";
import styles from "./TopBar.module.css";

export const TopBar = memo(function TopBar({
  health,
  apiError,
  onRetry,
  projectName,
  activePageLabel,
  onOpenAssistant,
  attentionPending = false,
  onOpenAttention,
  onOpenInspector,
  onBackToProjects,
  locale,
  onLocaleChange,
  version,
  versionFromBackend,
}: {
  health: boolean | null;
  apiError: string;
  onRetry: () => void;
  projectName: string;
  activePageLabel: string;
  onOpenAssistant: () => void;
  attentionPending?: boolean;
  onOpenAttention?: () => void;
  onOpenInspector: () => void;
  onBackToProjects: () => void;
  locale: LocalePreference;
  onLocaleChange: (locale: LocalePreference) => void;
  version: string;
  versionFromBackend: boolean;
}) {
  const { t } = useI18n();
  const [copyStatus, setCopyStatus] = useState("");
  const healthDotClass =
    health === true
      ? `${styles.healthDot} ${styles.healthOnline}`
      : health === false
        ? `${styles.healthDot} ${styles.healthOffline}`
        : `${styles.healthDot} ${styles.healthChecking}`;
  const healthLabel =
    health === true
      ? t("health.connected")
      : health === false
        ? t("health.offline")
        : t("health.connecting");
  const handleCopyDiagnostics = useCallback(async () => {
    const diagnostics = [
      `${t("topbar.healthLabel")}: ${healthLabel}`,
      `${t("topbar.projectLabel")}: ${projectName || t("projects.switcher.select")}`,
      `${t("topbar.workspaceLabel")}: ${activePageLabel}`,
      `${t("topbar.errorLabel")}: ${apiError || t("topbar.none")}`,
    ].join("\n");
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error(t("health.clipboardUnavailable"));
      }
      await navigator.clipboard.writeText(diagnostics);
      setCopyStatus(t("health.diagnosticsCopied"));
    } catch {
      setCopyStatus(t("health.clipboardUnavailable"));
    }
  }, [activePageLabel, apiError, healthLabel, projectName, t]);

  return (
    <>
      <header className={styles.topbar}>
        <div className={styles.caption}>
          <span className={styles.spark}>M</span>
          <strong>{t("app.name")}</strong>
          <span
            className={styles.version}
            title={versionFromBackend ? t("topbar.backendVersion") : t("common.offline")}
          >
            v{version}
            {versionFromBackend ? "" : ` · ${t("topbar.offlineSuffix")}`}
          </span>
        </div>
        <div className={styles.context} aria-label={t("topbar.context")}>
          <button
            aria-label={t("nav.backToProjects")}
            className={styles.backButton}
            onClick={onBackToProjects}
            type="button"
          >
            <Icon height={15} name="arrow-left" width={15} />
          </button>
          <strong>{projectName || t("nav.projects")}</strong>
          <i aria-hidden="true" />
          <small>{activePageLabel}</small>
        </div>
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.healthButton}
            onClick={health === false ? onRetry : undefined}
            title={healthLabel}
            aria-label={healthLabel}
          >
            <span className={healthDotClass} aria-hidden="true" />
            <span className={styles.healthLabel}>{healthLabel}</span>
          </button>
          <Button
            aria-label={t("nav.assistant")}
            className={styles.assistantButton}
            leadingIcon={<SparkIcon />}
            onClick={onOpenAssistant}
            title={`${t("nav.assistant")} (Ctrl+J)`}
            variant="ghost"
          >
            {t("nav.assistant")}
          </Button>
          {attentionPending ? (
            <Button
              aria-label={t("agent.attentionItems")}
              className={styles.attentionButton}
              onClick={onOpenAttention}
              variant="secondary"
            >
              {t("agent.attentionItems")}
            </Button>
          ) : null}
          <Tooltip label={t("nav.inspector")}>
            <IconButton label={t("nav.inspector")} onClick={onOpenInspector} variant="ghost">
              <Icon height={16} name="inspector" width={16} />
            </IconButton>
          </Tooltip>
          <button
            aria-label={locale === "en" ? t("topbar.switchChinese") : t("topbar.switchEnglish")}
            className={styles.localeButton}
            onClick={() => onLocaleChange(locale === "en" ? "zh-CN" : "en")}
            type="button"
          >
            <Icon height={15} name="language" width={15} />
            <span>{locale === "en" ? "中文" : "EN"}</span>
          </button>
        </div>
      </header>
      {apiError ? (
        <div className={styles.banner} role="alert">
          <div className={styles.message}>
            <strong>{healthLabel}</strong>
            <span>{apiError}</span>
            {copyStatus ? <small>{copyStatus}</small> : null}
          </div>
          <div className={styles.bannerActions}>
            <Button onClick={onRetry} size="sm" variant="secondary">
              {t("common.retry")}
            </Button>
            <Button onClick={handleCopyDiagnostics} size="sm" variant="secondary">
              {t("health.copyDiagnostics")}
            </Button>
          </div>
        </div>
      ) : null}
    </>
  );
});

function SparkIcon() {
  return (
    <svg
      className={styles.assistantIcon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
      aria-hidden="true"
    >
      <path
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.6"
        d="M8 2.5l1.1 3.2L12.5 7 9.1 8.3 8 11.5 6.9 8.3 3.5 7l3.4-1.3L8 2.5z"
      />
    </svg>
  );
}
