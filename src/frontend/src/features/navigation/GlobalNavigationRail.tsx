import { Fragment, useEffect, useState } from "react";
import { Icon } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import type { MessageKey } from "../../i18n/messages/en";
import type { AppLocation, ProjectWorkspace } from "./workspaceModel";
import styles from "./GlobalNavigationRail.module.css";

type RailItem = {
  icon: "folder" | "spark" | "runs" | "settings";
  key: MessageKey;
  workspace?: ProjectWorkspace;
};

const NAVIGATION_STORAGE_KEY = "medimage.navigationExpanded";

const items: RailItem[] = [
  { icon: "folder", key: "nav.projects" },
  { icon: "spark", key: "nav.agent", workspace: "agent" },
  { icon: "runs", key: "nav.runs", workspace: "runs" },
  { icon: "settings", key: "nav.settings", workspace: "settings" },
];

function readExpandedPreference(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const stored = window.localStorage.getItem(NAVIGATION_STORAGE_KEY);
    if (stored === "true" || stored === "false") return stored === "true";
  } catch {
    // The responsive default still works when local storage is unavailable.
  }
  return window.matchMedia?.("(min-width: 1440px)").matches ?? false;
}

export function GlobalNavigationRail({
  location,
  projectId,
  onOpenProjects,
  onOpenWorkspace,
}: {
  location: AppLocation;
  projectId: string | null;
  onOpenProjects: () => void;
  onOpenWorkspace: (projectId: string, workspace: ProjectWorkspace) => void;
}) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(readExpandedPreference);

  useEffect(() => {
    try {
      window.localStorage.setItem(NAVIGATION_STORAGE_KEY, String(expanded));
    } catch {
      // Navigation remains usable for this session without stored preferences.
    }
  }, [expanded]);

  return (
    <nav aria-label={t("nav.primary")} className={styles.rail} data-expanded={expanded}>
      <div className={styles.brandRow}>
        <span className={styles.brandMark} aria-hidden="true">
          <Icon height={18} name="spark" width={18} />
        </span>
        <span className={styles.brandCopy}>
          <strong>{t("nav.brand")}</strong>
          <small>{t("nav.brandDescription")}</small>
        </span>
        <button
          aria-controls="primary-navigation-items"
          aria-expanded={expanded}
          aria-label={t(expanded ? "nav.collapse" : "nav.expand")}
          className={styles.toggle}
          onClick={() => setExpanded((value) => !value)}
          title={t(expanded ? "nav.collapse" : "nav.expand")}
          type="button"
        >
          <Icon height={16} name={expanded ? "chevron-left" : "chevron-right"} width={16} />
        </button>
      </div>
      <span className={styles.sectionLabel}>{t("nav.workspaceGroup")}</span>
      <div className={styles.items} id="primary-navigation-items">
        {items.map((item, index) => {
          const isProjects = !item.workspace;
          const selected = isProjects
            ? location.kind === "projects"
            : location.kind !== "projects" && location.workspace === item.workspace;
          const disabled = !isProjects && !projectId;

          return (
            <Fragment key={item.key}>
              <button
                aria-current={selected ? "page" : undefined}
                aria-label={t(item.key)}
                className={styles.item}
                data-selected={selected}
                disabled={disabled}
                onClick={() => {
                  if (isProjects) {
                    onOpenProjects();
                    return;
                  }
                  if (!projectId || !item.workspace) return;
                  onOpenWorkspace(projectId, item.workspace);
                }}
                title={disabled ? t("nav.selectProjectFirst") : t(item.key)}
                type="button"
              >
                <span className={styles.itemIcon} aria-hidden="true">
                  <Icon height={18} name={item.icon} width={18} />
                </span>
                <span className={styles.itemLabel}>{t(item.key)}</span>
              </button>
              {index === 0 ? <span className={styles.divider} aria-hidden="true" /> : null}
            </Fragment>
          );
        })}
      </div>
      <div className={styles.railFooter}>
        <span className={styles.footerDot} aria-hidden="true" />
        <span className={styles.footerLabel}>{t("nav.localResearchWorkspace")}</span>
      </div>
    </nav>
  );
}
