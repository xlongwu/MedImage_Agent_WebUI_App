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

const items: RailItem[] = [
  { icon: "folder", key: "nav.projects" },
  { icon: "spark", key: "nav.agent", workspace: "agent" },
  { icon: "runs", key: "nav.runs", workspace: "runs" },
  { icon: "settings", key: "nav.settings", workspace: "settings" },
];

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

  return (
    <nav className={styles.rail} aria-label={t("nav.primary")}>
      <div className={styles.items}>
        {items.map((item, index) => {
          const isProjects = !item.workspace;
          const selected = isProjects
            ? location.kind === "projects"
            : location.kind !== "projects" && location.workspace === item.workspace;
          const disabled = !isProjects && !projectId;
          return (
            <button
              aria-current={selected ? "page" : undefined}
              aria-label={t(item.key)}
              className={styles.item}
              data-selected={selected}
              disabled={disabled}
              key={item.key}
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
              <Icon height={18} name={item.icon} width={18} />
              {index === 0 ? <span className={styles.divider} aria-hidden="true" /> : null}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
