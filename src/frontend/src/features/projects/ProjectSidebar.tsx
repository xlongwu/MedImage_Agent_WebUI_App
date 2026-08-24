import { useMemo, useState } from "react";

import { Button, Icon } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import type { ProjectSummary } from "../../lib/types/project";
import styles from "./ProjectSidebar.module.css";

export function ProjectSidebar({
  projects,
  selectedProjectId,
  onCreateProject,
  onSelectProject,
}: {
  projects: ProjectSummary[];
  selectedProjectId: string | null;
  onCreateProject: () => void;
  onSelectProject: (projectId: string) => void;
}) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const visibleProjects = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return projects;
    return projects.filter((project) =>
      [project.name, project.study_id, project.modality].some((value) =>
        value.toLowerCase().includes(needle),
      ),
    );
  }, [projects, query]);

  return (
    <aside className={styles.sidebar} aria-label={t("projects.contextSidebar")}>
      <header className={styles.header}>
        <div>
          <span>{t("projects.library")}</span>
          <strong>{t("nav.projects")}</strong>
        </div>
        <Button aria-label={t("projects.add")} onClick={onCreateProject} size="sm" variant="ghost">
          <Icon height={15} name="plus" width={15} />
        </Button>
      </header>
      <label className={styles.search}>
        <span className={styles.srOnly}>{t("projects.search")}</span>
        <input
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("projects.searchPlaceholder")}
          type="search"
          value={query}
        />
      </label>
      <div className={styles.list}>
        {visibleProjects.map((project) => {
          const selected = project.id === selectedProjectId;
          return (
            <button
              aria-current={selected ? "page" : undefined}
              className={styles.project}
              data-selected={selected}
              key={project.id}
              onClick={() => onSelectProject(project.id)}
              type="button"
            >
              <span className={styles.projectIcon}>
                <Icon height={16} name="folder" width={16} />
              </span>
              <span className={styles.projectText}>
                <strong>{project.name}</strong>
                <small>{project.study_id}</small>
              </span>
              <span
                aria-label={
                  project.latest_agent_task
                    ? t(`agent.currentAction.${project.latest_agent_task.current_action_code}`)
                    : t("projects.agentNotStarted")
                }
                className={styles.stateDot}
                data-ready={project.latest_agent_task?.state === "completed"}
              />
            </button>
          );
        })}
        {!visibleProjects.length ? <p className={styles.empty}>{t("projects.noMatches")}</p> : null}
      </div>
      <Button
        className={styles.createButton}
        leadingIcon={<Icon height={15} name="plus" width={15} />}
        onClick={onCreateProject}
        size="sm"
        variant="secondary"
      >
        {t("projects.add")}
      </Button>
    </aside>
  );
}
