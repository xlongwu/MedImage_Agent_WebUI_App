import { useEffect, useMemo, useRef, useState } from "react";

import { AlertBanner, Badge, Button, Dialog, EmptyState, Icon } from "../../components/ui";
import { formatNumber } from "../../i18n/format";
import { useI18n } from "../../i18n/useI18n";
import { RESULT_SUMMARY_KEYS } from "../agent/components/agentTaskMessages";
import type { ProjectSummary } from "../../lib/types/project";
import styles from "./ProjectsPage.module.css";

type TaskStatusFilter = "all" | "attention" | "active" | "completed";
type ModalityFilter = "all" | "rsfmri" | "mri";
type ProjectSort = "recent" | "name" | "subjects";

export interface ProjectsPageProps {
  deletingProjectId: string | null;
  error: string;
  loading: boolean;
  onClose?: () => void;
  onCreateProject: () => void;
  onDeleteProject: (id: string, name: string) => void;
  onRetry?: () => void | Promise<unknown>;
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
  onRetry,
  onSelectProject,
  projects,
  selectedProjectId,
}: ProjectsPageProps) {
  const { locale, t } = useI18n();
  const [query, setQuery] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState<TaskStatusFilter>("all");
  const [modalityFilter, setModalityFilter] = useState<ModalityFilter>("all");
  const [sortBy, setSortBy] = useState<ProjectSort>("recent");
  const [confirmDelete, setConfirmDelete] = useState<{ id: string; name: string } | null>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const taskStatusFilters: Array<{ id: TaskStatusFilter; label: string }> = [
    { id: "all", label: t("projects.all") },
    { id: "attention", label: t("projects.agentAttention") },
    { id: "active", label: t("projects.agentActive") },
    { id: "completed", label: t("projects.agentCompleted") },
  ];
  const modalityFilters: Array<{ id: ModalityFilter; label: string }> = [
    { id: "all", label: t("projects.modalityAll") },
    { id: "rsfmri", label: "rs-fMRI" },
    { id: "mri", label: "MRI" },
  ];

  const taskSummary = useMemo(() => {
    let active = 0;
    let attention = 0;
    for (const project of projects) {
      const task = project.latest_agent_task;
      if (!task) continue;
      if (task.state === "preparing" || task.state === "running") active += 1;
      if (requiresAttention(task)) attention += 1;
    }
    return { active, attention };
  }, [projects]);

  const filteredProjects = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase(locale);
    return projects
      .filter((project) => {
        const task = project.latest_agent_task;
        const modality = project.modality.toLowerCase().replace(/[\s_-]/g, "");
        const haystack = [project.name, project.study_id, project.modality, task?.goal_summary]
          .join(" ")
          .toLocaleLowerCase(locale);
        const matchesTaskStatus =
          taskStatusFilter === "all" ||
          (taskStatusFilter === "attention" && requiresAttention(task)) ||
          (taskStatusFilter === "active" && isActive(task)) ||
          (taskStatusFilter === "completed" && task?.state === "completed");
        const matchesModality =
          modalityFilter === "all" ||
          (modalityFilter === "rsfmri" && modality.includes("rsfmri")) ||
          (modalityFilter === "mri" && modality.includes("mri") && !modality.includes("rsfmri"));
        return (!needle || haystack.includes(needle)) && matchesTaskStatus && matchesModality;
      })
      .sort((left, right) => {
        if (sortBy === "name") return left.name.localeCompare(right.name, locale);
        if (sortBy === "subjects") return right.subjects_count - left.subjects_count;
        return compareRecentProjects(left, right);
      });
  }, [locale, modalityFilter, projects, query, sortBy, taskStatusFilter]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (confirmDelete || event.key !== "/" || event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      const target = event.target;
      if (
        target instanceof HTMLElement &&
        (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      event.preventDefault();
      searchInputRef.current?.focus();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmDelete]);

  const selectProject = (projectId: string) => {
    onSelectProject(projectId);
    onClose?.();
  };

  const clearFilters = () => {
    setQuery("");
    setTaskStatusFilter("all");
    setModalityFilter("all");
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
          disabled={Boolean(deletingProjectId)}
          leadingIcon={<Icon height={16} name="plus" width={16} />}
          onClick={onCreateProject}
          variant="primary"
        >
          {t("projects.add")}
        </Button>
      </header>

      {projects.length > 0 ? (
        <section aria-label={t("projects.summary.overview")} className={styles.summary}>
          <article className={styles.summaryMetric}>
            <span>{t("projects.summary.total")}</span>
            <strong>{formatNumber(locale, projects.length)}</strong>
            <small>{t("projects.summary.totalDetail")}</small>
          </article>
          <article className={styles.summaryMetric} data-tone="info">
            <span>{t("projects.summary.active")}</span>
            <strong>{formatNumber(locale, taskSummary.active)}</strong>
            <small>{t("projects.summary.activeDetail")}</small>
          </article>
          <article className={styles.summaryMetric} data-tone="warning">
            <span>{t("projects.summary.attention")}</span>
            <strong>{formatNumber(locale, taskSummary.attention)}</strong>
            <small>{t("projects.summary.attentionDetail")}</small>
          </article>
        </section>
      ) : null}

      <div className={styles.toolbar}>
        <label className={styles.search}>
          <span>{t("projects.search")}</span>
          <span className={styles.searchField}>
            <input
              aria-keyshortcuts="/"
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("projects.searchPlaceholder")}
              ref={searchInputRef}
              type="search"
              value={query}
            />
            <kbd aria-hidden="true">/</kbd>
          </span>
        </label>
        <div className={styles.filterGroups} role="group" aria-label={t("projects.filters")}>
          <div
            className={styles.filterGroup}
            role="group"
            aria-label={t("projects.filters.taskStatus")}
          >
            <span className={styles.filterLabel}>{t("projects.filters.taskStatus")}</span>
            <div className={styles.filters}>
              {taskStatusFilters.map((filter) => (
                <button
                  aria-pressed={taskStatusFilter === filter.id}
                  className={styles.filterButton}
                  key={filter.id}
                  onClick={() => setTaskStatusFilter(filter.id)}
                  type="button"
                >
                  {filter.label}
                </button>
              ))}
            </div>
          </div>
          <div
            className={styles.filterGroup}
            role="group"
            aria-label={t("projects.filters.modality")}
          >
            <span className={styles.filterLabel}>{t("projects.filters.modality")}</span>
            <div className={styles.filters}>
              {modalityFilters.map((filter) => (
                <button
                  aria-pressed={modalityFilter === filter.id}
                  className={styles.filterButton}
                  key={filter.id}
                  onClick={() => setModalityFilter(filter.id)}
                  type="button"
                >
                  {filter.label}
                </button>
              ))}
            </div>
          </div>
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
        <AlertBanner
          action={
            onRetry ? (
              <Button
                disabled={loading}
                onClick={() => void onRetry()}
                size="sm"
                variant="secondary"
              >
                {t("common.retry")}
              </Button>
            ) : undefined
          }
          title={t("projects.refreshFailed")}
          tone="warning"
        >
          {error}
        </AlertBanner>
      ) : null}

      {loading && projects.length > 0 && !error ? (
        <div className={styles.refreshing} role="status">
          {t("projects.refreshing")}
        </div>
      ) : null}

      {loading && projects.length === 0 ? (
        <div aria-label={t("projects.loading")} className={styles.skeletonGrid} role="status">
          {Array.from({ length: 3 }).map((_, index) => (
            <span className={styles.skeletonCard} key={index} />
          ))}
        </div>
      ) : error && projects.length === 0 ? (
        <EmptyState
          action={
            onRetry ? (
              <Button disabled={loading} onClick={() => void onRetry()} variant="primary">
                {t("common.retry")}
              </Button>
            ) : undefined
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
        <EmptyState
          action={
            <Button onClick={clearFilters} variant="secondary">
              {t("projects.clearFilters")}
            </Button>
          }
          description={t("projects.noMatchesDescription")}
          title={t("projects.noMatches")}
        />
      ) : (
        <section aria-labelledby="projects-results-title" aria-busy={loading}>
          <div className={styles.resultsHeading}>
            <h2 id="projects-results-title">{t("projects.results")}</h2>
            <p aria-live="polite">
              {t("projects.visibleCount", { count: formatNumber(locale, filteredProjects.length) })}
            </p>
          </div>
          <div className={styles.list} role="list">
            {filteredProjects.map((project) => {
              const task = project.latest_agent_task;
              const taskStatus = getTaskStatus(task, t);
              const taskTone = getTaskTone(task);
              const updatedAt = task?.updated_at ?? project.created_date;

              return (
                <article
                  aria-busy={deletingProjectId === project.id}
                  className={styles.row}
                  data-selected={project.id === selectedProjectId}
                  key={project.id}
                  role="listitem"
                >
                  <button
                    aria-current={project.id === selectedProjectId ? "page" : undefined}
                    aria-label={t("projects.openProject", { name: project.name })}
                    className={styles.rowMain}
                    onClick={() => selectProject(project.id)}
                    type="button"
                  >
                    <span className={styles.cardTopline}>
                      <span className={styles.projectIcon}>
                        <Icon height={18} name="folder" width={18} />
                      </span>
                      <Badge size="sm" tone="neutral">
                        {project.modality || t("common.unavailable")}
                      </Badge>
                      <span aria-hidden="true" className={styles.openGlyph}>
                        <Icon height={14} name="arrow-up-right" width={14} />
                      </span>
                    </span>
                    <span className={styles.projectIdentity}>
                      <span aria-level={3} className={styles.projectName} role="heading">
                        {project.name}
                      </span>
                      <small>{project.study_id || t("common.unavailable")}</small>
                    </span>
                    <span className={styles.cardFacts}>
                      <span>
                        <strong>{formatNumber(locale, project.subjects_count)}</strong>
                        <small>{t("projects.subjects")}</small>
                      </span>
                      <span>
                        <strong className={styles.state} data-tone={taskTone}>
                          <i aria-hidden="true" />
                          {taskStatus}
                        </strong>
                        <small>{t("projects.taskStatus")}</small>
                      </span>
                    </span>
                    <span className={styles.taskSummary}>
                      {task?.result_summary_code ? (
                        <span>{t(RESULT_SUMMARY_KEYS[task.result_summary_code].title)}</span>
                      ) : task ? (
                        <span>{t(`agent.currentAction.${task.current_action_code}`)}</span>
                      ) : (
                        <span>{t("projects.agentNotStarted")}</span>
                      )}
                      {task?.goal_summary ? <small>{task.goal_summary}</small> : null}
                    </span>
                    <span className={styles.cardTimestamp}>
                      <span>{t("projects.lastActivity")}</span>
                      <time dateTime={toIsoDateTime(updatedAt)}>
                        {formatProjectDate(updatedAt, locale, t)}
                      </time>
                    </span>
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
                      aria-label={t("projects.removeProject", { name: project.name })}
                      disabled={Boolean(deletingProjectId)}
                      onClick={() => setConfirmDelete({ id: project.id, name: project.name })}
                      size="sm"
                      variant="ghost"
                    >
                      {deletingProjectId === project.id
                        ? t("projects.removing")
                        : t("common.remove")}
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
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

function requiresAttention(task: ProjectSummary["latest_agent_task"]): boolean {
  return Boolean(
    task &&
    (task.requires_user || task.state === "needs_attention" || task.state === "waiting_for_user"),
  );
}

function isActive(task: ProjectSummary["latest_agent_task"]): boolean {
  return task?.state === "preparing" || task?.state === "running";
}

function getTaskStatus(
  task: ProjectSummary["latest_agent_task"],
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (!task) return t("projects.agentNotStarted");
  if (requiresAttention(task)) return t("projects.agentAttention");
  if (task.state === "completed") return t("projects.agentCompleted");
  return t("projects.agentActive");
}

function getTaskTone(task: ProjectSummary["latest_agent_task"]): string {
  if (!task) return "neutral";
  if (requiresAttention(task)) return "warning";
  if (task.state === "completed" && task.outcome === "failed") return "danger";
  if (task.state === "completed" && task.outcome === "succeeded") return "success";
  if (task.state === "completed" && task.outcome === "partial") return "warning";
  return "info";
}

function compareRecentProjects(left: ProjectSummary, right: ProjectSummary): number {
  const leftDate = left.latest_agent_task?.updated_at ?? left.created_date;
  const rightDate = right.latest_agent_task?.updated_at ?? right.created_date;
  const leftTime = Date.parse(leftDate);
  const rightTime = Date.parse(rightDate);
  if (Number.isFinite(leftTime) && Number.isFinite(rightTime)) return rightTime - leftTime;
  return rightDate.localeCompare(leftDate);
}

function formatProjectDate(
  value: string,
  locale: string,
  t: ReturnType<typeof useI18n>["t"],
): string {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date(value);
  if (!value || Number.isNaN(date.getTime())) return value || t("common.unavailable");
  const hasTime = /[T ]\d{2}:\d{2}/.test(value);
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    ...(hasTime ? { timeStyle: "short" as const } : {}),
  }).format(date);
}

function toIsoDateTime(value: string): string | undefined {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : undefined;
}
