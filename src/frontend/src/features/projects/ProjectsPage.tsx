import { useMemo, useState } from "react";

import { Badge, Button, Dialog, EmptyState, Icon } from "../../components/ui";
import { useI18n } from "../../i18n/useI18n";
import type { ProjectSummary } from "../../lib/types/project";
import styles from "./ProjectsPage.module.css";

type ProjectFilter = "all" | "attention" | "active" | "completed" | "rsfmri" | "mri";
type ProjectSort = "recent" | "name" | "subjects";

export interface ProjectsPageProps {
  deletingProjectId: string | null;
  error: string;
  loading: boolean;
  onClose?: () => void;
  onCreateProject: () => void;
  onDeleteProject: (id: string, name: string) => void;
  onSelectProject: (id: string) => void;
  projects: ProjectSummary[];
  selectedProjectId: string | null;
}

export function ProjectsPage({
  deletingProjectId,
  error,
  loading,
  onClose,
  onCreateProject,
  onDeleteProject,
  onSelectProject,
  projects,
  selectedProjectId,
}: ProjectsPageProps) {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<ProjectFilter>("all");
  const [sortBy, setSortBy] = useState<ProjectSort>("recent");
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; name: string } | null>(null);
  const filters: Array<{ id: ProjectFilter; label: string }> = [
    { id: "all", label: t("projects.all") },
    { id: "attention", label: t("projects.agentAttention") },
    { id: "active", label: t("projects.agentActive") },
    { id: "completed", label: t("projects.agentCompleted") },
    { id: "rsfmri", label: "rs-fMRI" },
    { id: "mri", label: "MRI" },
  ];

  const filteredProjects = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return projects
      .filter((project) => {
        const task = project.latest_agent_task;
        const haystack = [
          project.name,
          project.study_id,
          project.modality,
          task?.goal_summary,
          task?.current_action,
        ]
          .join(" ")
          .toLowerCase();
        return (
          (!needle || haystack.includes(needle)) &&
          (activeFilter === "all" ||
            (activeFilter === "attention" && task?.requires_user === true) ||
            (activeFilter === "active" &&
              (task?.state === "preparing" || task?.state === "running")) ||
            (activeFilter === "completed" && task?.state === "completed") ||
            (activeFilter === "rsfmri" && project.modality.toLowerCase().includes("rs-fmri")) ||
            (activeFilter === "mri" && project.modality.toLowerCase().includes("mri")))
        );
      })
      .sort((left, right) => {
        if (sortBy === "name") return left.name.localeCompare(right.name);
        if (sortBy === "subjects") return right.subjects_count - left.subjects_count;
        return String(right.created_date).localeCompare(String(left.created_date));
      });
  }, [activeFilter, projects, query, sortBy]);

  const selectProject = (projectId: string) => {
    onSelectProject(projectId);
    onClose?.();
  };

  return (
    <section className={styles.page} aria-labelledby="projects-page-title">
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>{t("projects.library")}</p>
          <h1 className={styles.title} id="projects-page-title">
            {t("projects.title")}
          </h1>
          <p className={styles.subtitle}>{t("projects.subtitle")}</p>
        </div>
        <Button
          leadingIcon={<Icon height={16} name="plus" width={16} />}
          onClick={onCreateProject}
          variant="primary"
        >
          {t("projects.add")}
        </Button>
      </header>

      <div className={styles.toolbar}>
        <label className={styles.search}>
          <span>{t("projects.search")}</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("projects.searchPlaceholder")}
            type="search"
            value={query}
          />
        </label>
        <div className={styles.filters} aria-label={t("projects.filters")}>
          {filters.map((filter) => (
            <button
              aria-pressed={activeFilter === filter.id}
              className={styles.filterButton}
              key={filter.id}
              onClick={() => setActiveFilter(filter.id)}
              type="button"
            >
              {filter.label}
            </button>
          ))}
        </div>
        <label className={styles.sort}>
          <span>{t("projects.sort.label")}</span>
          <select
            aria-label={t("projects.sort.label")}
            onChange={(event) => setSortBy(event.target.value as ProjectSort)}
            value={sortBy}
          >
            <option value="recent">{t("projects.sort.recent")}</option>
            <option value="name">{t("projects.sort.name")}</option>
            <option value="subjects">{t("projects.sort.subjects")}</option>
          </select>
        </label>
      </div>

      {error && projects.length > 0 ? (
        <div className={styles.warning} role="status">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div aria-label={t("projects.loading")} className={styles.list} role="status">
          {Array.from({ length: 6 }).map((_, index) => (
            <span className={styles.skeletonRow} key={index} />
          ))}
        </div>
      ) : error && projects.length === 0 ? (
        <EmptyState
          action={
            <Button onClick={onCreateProject} variant="primary">
              {t("projects.add")}
            </Button>
          }
          description={`${t("projects.errorDescription")} ${error}`}
          icon={<Icon height={22} name="circle-alert" width={22} />}
          title={t("projects.errorTitle")}
        />
      ) : projects.length === 0 ? (
        <EmptyState
          action={
            <Button onClick={onCreateProject} variant="primary">
              {t("projects.emptyAction")}
            </Button>
          }
          description={t("projects.emptyDescription")}
          icon={<Icon height={22} name="folder" width={22} />}
          title={t("projects.emptyTitle")}
        />
      ) : filteredProjects.length === 0 ? (
        <EmptyState title={t("projects.noMatches")} />
      ) : (
        <div className={styles.list} role="list">
          <div className={styles.listHeader} aria-hidden="true">
            <span>{t("projects.column.project")}</span>
            <span>{t("projects.column.dataset")}</span>
            <span>{t("projects.column.status")}</span>
            <span>{t("projects.column.updated")}</span>
            <span />
          </div>
          {filteredProjects.map((project) => {
            const task = project.latest_agent_task;
            return (
              <article
                className={styles.row}
                data-selected={project.id === selectedProjectId}
                key={project.id}
                role="listitem"
              >
                <button
                  className={styles.rowMain}
                  onClick={() => selectProject(project.id)}
                  type="button"
                >
                  <span className={styles.projectIcon}>
                    <Icon height={18} name="folder" width={18} />
                  </span>
                  <span className={styles.projectIdentity}>
                    <h2>{project.name}</h2>
                    <span>{project.study_id}</span>
                  </span>
                  <span className={styles.datasetFacts}>
                    <Badge tone="neutral">{project.modality || t("common.unavailable")}</Badge>
                    <small>{t("projects.subjectCount", { count: project.subjects_count })}</small>
                  </span>
                  <span className={styles.taskStatus} title={task?.goal_summary}>
                    <span className={styles.state} data-state={task?.state ?? "not-started"}>
                      <i aria-hidden="true" />
                      {task
                        ? t(`agent.currentAction.${task.current_action_code}`)
                        : t("projects.agentNotStarted")}
                    </span>
                    {task?.result_title ? <small>{task.result_title}</small> : null}
                  </span>
                  <time>
                    {task?.updated_at ?? (project.created_date || t("common.unavailable"))}
                  </time>
                </button>
                <div className={styles.rowActions}>
                  <Button
                    onClick={() => selectProject(project.id)}
                    size="sm"
                    variant={project.id === selectedProjectId ? "primary" : "secondary"}
                  >
                    {project.id === selectedProjectId ? t("common.open") : t("common.select")}
                  </Button>
                  <Button
                    disabled={deletingProjectId === project.id}
                    onClick={() => setConfirmDelete({ id: project.id, name: project.name })}
                    size="sm"
                    variant="ghost"
                  >
                    {t("common.remove")}
                  </Button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      <Dialog
        description={
          confirmDelete ? t("projects.removeDescription", { name: confirmDelete.name }) : null
        }
        footer={
          confirmDelete ? (
            <>
              <Button onClick={() => setConfirmDelete(null)} variant="secondary">
                {t("common.cancel")}
              </Button>
              <Button
                disabled={deletingProjectId === confirmDelete.id}
                onClick={() => {
                  onDeleteProject(confirmDelete.id, confirmDelete.name);
                  setConfirmDelete(null);
                }}
                variant="danger"
              >
                {t("common.remove")}
              </Button>
            </>
          ) : null
        }
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
        open={Boolean(confirmDelete)}
        title={t("projects.removeTitle")}
      />
    </section>
  );
}
