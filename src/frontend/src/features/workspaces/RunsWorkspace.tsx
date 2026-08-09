import { useMemo, useState } from "react";
import type { TaskDiagnostics, TaskEvent, TaskLogEntry, TaskStatus } from "../../lib/types/task";
import type { AgentTaskResponse } from "../../lib/types/agentTask";
import type {
  ProjectRunDetailResponse,
  ProjectRunEventsResponse,
  ProjectRunLogsResponse,
  ProjectRunStateTimelineResponse,
} from "../../types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Icon,
  SegmentedControl,
  Table,
  TableEmpty,
} from "../../components/ui";
import { WorkspaceHeader } from "../dashboard/DashboardChrome";
import styles from "./RunsWorkspace.module.css";
import layoutStyles from "./WorkspaceLayout.module.css";
import { useI18n } from "../../i18n/useI18n";
import { getAgentResultMessageKey } from "../agent/components/ResultSummaryCard";
import { useProjectRunDetails, type ProjectRunDetails } from "../runs/useProjectRunDetails";

export interface RunsWorkspaceProps {
  agentTask?: AgentTaskResponse | null;
  baseUrl: string;
  error: string;
  loading: boolean;
  onRetryTasks: () => void;
  onOpenAgent?: () => void;
  onSelectTask: (taskId: string) => void;
  projectId: string | null;
  selectedTask: TaskLogEntry | null;
  selectedTaskId: string | null;
  tasks: TaskLogEntry[];
  historyTasks: TaskLogEntry[];
}

type RunStatusFilter = "all" | "active" | "failed" | "completed";
type RunDetailTab = "events" | "logs" | "diagnostics" | "artifacts" | "audit";
type RunWorkspaceView = "workspace" | "history";
type Translate = ReturnType<typeof useI18n>["t"];

const RUN_LIST_RENDER_LIMIT = 50;
const RUN_LOG_RENDER_LIMIT = 12;
const DIAGNOSIS_RENDER_LIMIT = 8;
const EXTERNAL_TOOL_RENDER_LIMIT = 4;

export function RunsWorkspace({
  agentTask,
  baseUrl,
  error,
  loading,
  onOpenAgent,
  onRetryTasks,
  onSelectTask,
  projectId,
  selectedTask,
  selectedTaskId,
  tasks,
  historyTasks,
}: RunsWorkspaceProps) {
  const { t } = useI18n();
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<RunStatusFilter>("all");
  const [detailTab, setDetailTab] = useState<RunDetailTab>("events");
  const [workspaceView, setWorkspaceView] = useState<RunWorkspaceView>("workspace");
  const runDetails = useProjectRunDetails(baseUrl, projectId, selectedTask?.id ?? null);
  const hasProject = Boolean(projectId);
  const sourceTasks = workspaceView === "history" ? historyTasks : tasks;
  const filteredTasks = useMemo(
    () =>
      sourceTasks.filter((task) => {
        const query = searchTerm.trim().toLowerCase();
        const matchesQuery =
          !query ||
          [
            task.id,
            task.run_name,
            task.pipeline,
            task.dataset,
            task.owner,
            task.execution_mode ?? "",
          ].some((value) => value.toLowerCase().includes(query));
        const matchesStatus =
          statusFilter === "all" ||
          (statusFilter === "active" && (task.status === "running" || task.status === "pending")) ||
          (statusFilter === "failed" && task.status === "failed") ||
          (statusFilter === "completed" &&
            (task.status === "completed" || task.status === "partial"));

        return matchesQuery && matchesStatus;
      }),
    [searchTerm, sourceTasks, statusFilter],
  );
  const visibleTasks = filteredTasks.slice(0, RUN_LIST_RENDER_LIMIT);
  const isFiltered = searchTerm.trim().length > 0 || statusFilter !== "all";
  const emptyRunListMessage = runListEmptyMessage({
    error,
    filtered: isFiltered,
    loading,
    projectId,
    taskCount: sourceTasks.length,
    t,
  });

  return (
    <div className={layoutStyles.stack}>
      <WorkspaceHeader
        title={t("runs.title")}
        subtitle={t("runs.subtitle")}
        status={hasProject ? t("runs.header.history") : t("runs.header.selectProject")}
      />

      <div className={styles.workspaceViewSwitch}>
        <SegmentedControl
          aria-label={t("runs.workspaceView")}
          onChange={(value) => setWorkspaceView(value as RunWorkspaceView)}
          options={[
            { label: t("runs.view.workspace"), value: "workspace" },
            { label: t("runs.view.history"), value: "history" },
          ]}
          value={workspaceView}
        />
      </div>

      {!hasProject ? (
        <EmptyState
          title={t("runs.noProject.title")}
          description={t("runs.noProject.description")}
        />
      ) : workspaceView === "history" ? (
        <RunsOverview tasks={historyTasks} />
      ) : null}

      {workspaceView === "history" && agentTask?.project_id === projectId ? (
        <AgentTaskEvidencePanel task={agentTask} />
      ) : null}

      {workspaceView === "workspace" && hasProject ? (
        <section className={styles.taskWorkspace} aria-label={t("runs.taskWorkspaceAria")}>
          <Card className={styles.taskSidebar} tone="muted">
            <div className={styles.sectionHeader}>
              <div>
                <h3>{t("runs.tasks.title")}</h3>
                <p>{t("runs.tasks.description")}</p>
              </div>
              {error ? (
                <Button size="sm" variant="secondary" onClick={onRetryTasks}>
                  {t("common.retry")}
                </Button>
              ) : null}
            </div>
            <RunFilters
              searchTerm={searchTerm}
              setSearchTerm={setSearchTerm}
              setStatusFilter={setStatusFilter}
              statusFilter={statusFilter}
            />
            {loading && sourceTasks.length ? (
              <div className={styles.loadingLine}>{t("runs.refreshing")}</div>
            ) : null}
            {error ? (
              <div className={styles.errorLine}>
                {sourceTasks.length ? t("runs.refreshFailedStale") : ""}
                {error}
              </div>
            ) : null}
            <div className={styles.taskList}>
              {visibleTasks.length ? (
                visibleTasks.map((task) => (
                  <button
                    aria-current={task.id === selectedTaskId ? "true" : undefined}
                    className={styles.taskListItem}
                    data-selected={task.id === selectedTaskId}
                    key={task.id}
                    onClick={() => onSelectTask(task.id)}
                    type="button"
                  >
                    <span className={styles.taskListHeading}>
                      <strong>{task.run_name || task.pipeline || task.id}</strong>
                      <Badge size="sm" tone={statusTone(task.status)}>
                        {statusLabel(task.status, t)}
                      </Badge>
                    </span>
                    <span className={styles.taskListMeta}>
                      <small>{task.pipeline}</small>
                      <time>{task.started_at}</time>
                    </span>
                    <RunProgress value={task.progress} />
                  </button>
                ))
              ) : (
                <p className={styles.taskListEmpty}>{emptyRunListMessage}</p>
              )}
            </div>
          </Card>

          <Card className={styles.taskDetailCard}>
            {selectedTask ? (
              <SelectedRunProjection
                agentTask={
                  agentTaskMatchesRun(agentTask, selectedTask, projectId) ? agentTask : null
                }
                onOpenAgent={onOpenAgent}
                task={selectedTask}
              />
            ) : agentTask?.project_id === projectId &&
              agentTask.next_action.type === "approve_execution" ? (
              <ApprovalStatusPanel task={agentTask} onOpenAgent={onOpenAgent} />
            ) : (
              <div className={styles.approvalUnavailable}>
                <Icon name="circle-alert" width={18} height={18} />
                <div>
                  <strong>{t("runs.approval.unavailable")}</strong>
                  <p>{t("runs.approval.unavailableDescription")}</p>
                </div>
              </div>
            )}
            {selectedTask ? (
              <RunDetailPanel
                task={selectedTask}
                details={runDetails.data}
                loading={runDetails.loading}
                error={runDetails.error}
                onRetry={runDetails.reload}
                activeTab={detailTab}
                onTabChange={setDetailTab}
              />
            ) : (
              <EmptyState
                title={t("runs.selectRun.title")}
                description={t("runs.selectRun.description")}
              />
            )}
          </Card>

          <Card className={styles.artifactInspector}>
            <RunArtifactInspector
              details={runDetails.data}
              loading={runDetails.loading}
              task={selectedTask}
            />
          </Card>
        </section>
      ) : null}

      {workspaceView === "history" ? (
        <section className={styles.runLayout} aria-label={t("runs.layoutAria")}>
          <Card className={styles.runListCard} tone="muted">
            <div className={styles.sectionHeader}>
              <div>
                <h3>{t("runs.execution.title")}</h3>
                <p>{t("runs.execution.description")}</p>
              </div>
              <div className={styles.headerActions}>
                {error ? (
                  <Button size="sm" variant="secondary" onClick={onRetryTasks}>
                    {t("common.retry")}
                  </Button>
                ) : null}
              </div>
            </div>

            {hasProject ? (
              <RunFilters
                searchTerm={searchTerm}
                setSearchTerm={setSearchTerm}
                setStatusFilter={setStatusFilter}
                statusFilter={statusFilter}
              />
            ) : null}

            {loading && sourceTasks.length ? (
              <div className={styles.loadingLine}>{t("runs.refreshing")}</div>
            ) : null}
            {error ? (
              <div className={styles.errorLine}>
                {sourceTasks.length ? t("runs.refreshFailedStale") : ""}
                {error}
              </div>
            ) : null}

            {hasProject ? (
              <Table caption={t("runs.table.caption")}>
                <thead>
                  <tr>
                    <th>{t("runs.table.run")}</th>
                    <th>{t("runs.table.project")}</th>
                    <th>{t("runs.table.pipeline")}</th>
                    <th>{t("runs.table.status")}</th>
                    <th>{t("runs.table.progress")}</th>
                    <th>{t("runs.table.started")}</th>
                    <th>{t("runs.table.duration")}</th>
                    <th>{t("runs.table.triggeredBy")}</th>
                    <th>{t("runs.table.action")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleTasks.length ? (
                    visibleTasks.map((task) => (
                      <tr
                        key={task.id}
                        className={task.id === selectedTaskId ? styles.selectedRow : undefined}
                      >
                        <td>
                          <strong className={styles.runName}>{task.run_name}</strong>
                          <small className={styles.runMeta}>{task.id}</small>
                        </td>
                        <td>{task.dataset || projectId}</td>
                        <td>{task.pipeline}</td>
                        <td>
                          <Badge tone={statusTone(task.status)} size="sm">
                            {statusLabel(task.status, t)}
                          </Badge>
                        </td>
                        <td>
                          <RunProgress value={task.progress} />
                        </td>
                        <td>{task.started_at}</td>
                        <td>{task.duration || t("runs.inProgress")}</td>
                        <td>{task.owner}</td>
                        <td>
                          <Button
                            size="sm"
                            variant={task.id === selectedTaskId ? "primary" : "secondary"}
                            onClick={() => onSelectTask(task.id)}
                          >
                            {t("common.open")}
                          </Button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <TableEmpty colSpan={9}>{emptyRunListMessage}</TableEmpty>
                  )}
                </tbody>
              </Table>
            ) : (
              <EmptyState
                title={t("runs.listUnavailable.title")}
                description={t("runs.listUnavailable.description")}
              />
            )}
            {filteredTasks.length > visibleTasks.length ? (
              <div className={styles.trimNote}>
                {t("runs.trimmed", {
                  visible: visibleTasks.length,
                  total: filteredTasks.length,
                })}
              </div>
            ) : null}
          </Card>

          <Card className={styles.detailCard}>
            <div className={styles.sectionHeader}>
              <div>
                <h3>{t("runs.detail.title")}</h3>
                <p>{t("runs.detail.description")}</p>
              </div>
            </div>
            {selectedTask ? (
              <RunDetailPanel
                task={selectedTask}
                details={runDetails.data}
                loading={runDetails.loading}
                error={runDetails.error}
                onRetry={runDetails.reload}
                activeTab={detailTab}
                onTabChange={setDetailTab}
              />
            ) : (
              <EmptyState
                title={t("runs.selectRun.title")}
                description={t("runs.selectRun.description")}
              />
            )}
          </Card>
        </section>
      ) : null}
    </div>
  );
}

function RunFilters({
  searchTerm,
  setSearchTerm,
  setStatusFilter,
  statusFilter,
}: {
  searchTerm: string;
  setSearchTerm: (value: string) => void;
  setStatusFilter: (value: RunStatusFilter) => void;
  statusFilter: RunStatusFilter;
}) {
  const { t } = useI18n();
  return (
    <div className={styles.runControls}>
      <label className={styles.searchField}>
        <span>{t("runs.search")}</span>
        <input
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder={t("runs.searchPlaceholder")}
          value={searchTerm}
        />
      </label>
      <SegmentedControl
        aria-label={t("runs.filterAria")}
        onChange={(value) => setStatusFilter(value as RunStatusFilter)}
        options={[
          { label: t("runs.filter.all"), value: "all" },
          { label: t("runs.filter.active"), value: "active" },
          { label: t("runs.filter.failed"), value: "failed" },
          { label: t("runs.filter.completed"), value: "completed" },
        ]}
        value={statusFilter}
      />
    </div>
  );
}

function agentTaskMatchesRun(
  agentTask: AgentTaskResponse | null | undefined,
  run: TaskLogEntry,
  projectId: string | null,
): agentTask is AgentTaskResponse {
  if (!agentTask || agentTask.project_id !== projectId) return false;
  if (run.agent_task_id) {
    if (run.agent_task_id !== agentTask.task_id) return false;
    return !agentTask.technical_details?.run_id || agentTask.technical_details.run_id === run.id;
  }
  return Boolean(agentTask.technical_details?.run_id === run.id);
}

function SelectedRunProjection({
  agentTask,
  onOpenAgent,
  task,
}: {
  agentTask: AgentTaskResponse | null;
  onOpenAgent?: () => void;
  task: TaskLogEntry;
}) {
  const { t } = useI18n();
  const result = agentTask?.result_summary;
  const terminal = ["completed", "partial", "failed"].includes(task.status);
  const completedSubjects = result?.completed_subjects ?? agentTask?.progress.completed_subjects;
  const totalSubjects = result?.total_subjects ?? agentTask?.progress.total_subjects;
  const localizeResultText = (value: string) => {
    const key = getAgentResultMessageKey(value);
    return key ? t(key) : value;
  };
  const summary =
    (result?.summary ? localizeResultText(result.summary) : null) ||
    (agentTask?.current_action ?? t(`runs.projection.summary.${task.status}`));

  return (
    <section className={styles.approvalPanel} aria-label={t("runs.projection.title")}>
      <header>
        <div>
          <span>{t("runs.projection.gate")}</span>
          <h3>{result?.title ? localizeResultText(result.title) : task.run_name}</h3>
        </div>
        <Badge tone={statusTone(task.status)}>{statusLabel(task.status, t)}</Badge>
      </header>
      <p>{summary}</p>
      <div className={styles.approvalChecks}>
        <span data-ok={Boolean(agentTask)}>
          <Icon height={14} name={agentTask ? "circle-check" : "circle-alert"} width={14} />
          {agentTask
            ? t("runs.projection.agentTask", { id: agentTask.task_id })
            : t("runs.projection.noAgentTask")}
        </span>
        <span data-ok={Boolean(task.reviewed_plan_id)}>
          <Icon
            height={14}
            name={task.reviewed_plan_id ? "circle-check" : "circle-alert"}
            width={14}
          />
          {t("runs.projection.reviewedPlan")}
        </span>
        <span data-ok={terminal}>
          <Icon height={14} name={terminal ? "circle-check" : "circle-alert"} width={14} />
          {terminal ? t("runs.projection.terminal") : t("runs.projection.active")}
        </span>
      </div>
      {completedSubjects != null && totalSubjects != null ? (
        <p>
          {t("runs.projection.subjects", { completed: completedSubjects, total: totalSubjects })}
        </p>
      ) : null}
      {result?.limitations.length ? (
        <ul>
          {result.limitations.slice(0, 3).map((limitation) => (
            <li key={limitation}>{localizeResultText(limitation)}</li>
          ))}
        </ul>
      ) : null}
      <div className={styles.approvalFooter}>
        <small>{t("runs.projection.authority")}</small>
        {agentTask && onOpenAgent ? (
          <Button onClick={onOpenAgent} size="sm" variant="secondary">
            {t("runs.projection.openAgent")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function ApprovalStatusPanel({
  onOpenAgent,
  task,
}: {
  onOpenAgent?: () => void;
  task: AgentTaskResponse;
}) {
  const { t } = useI18n();
  const summary = task.approval_summary;
  const awaitingApproval = task.next_action.type === "approve_execution";
  const tone =
    task.state === "completed"
      ? "success"
      : task.state === "needs_attention"
        ? "danger"
        : awaitingApproval
          ? "warning"
          : "info";
  return (
    <section className={styles.approvalPanel} aria-label={t("runs.approval.title")}>
      <header>
        <div>
          <span>{t("runs.approval.gate")}</span>
          <h3>{t("runs.approval.title")}</h3>
        </div>
        <Badge tone={tone}>{t(`agent.state.${task.state}`)}</Badge>
      </header>
      <p>{summary?.execution_summary || task.current_action}</p>
      <div className={styles.approvalChecks}>
        <span data-ok={summary?.rawdata_read_only ?? false}>
          <Icon
            height={14}
            name={summary?.rawdata_read_only ? "circle-check" : "circle-alert"}
            width={14}
          />
          {t("runs.approval.rawdataReadonly")}
        </span>
        <span data-ok={Boolean(summary?.summary_hash)}>
          <Icon
            height={14}
            name={summary?.summary_hash ? "circle-check" : "circle-alert"}
            width={14}
          />
          {t("runs.approval.summaryBound")}
        </span>
        <span data-ok={Boolean(task.technical_details?.plan_hash)}>
          <Icon
            height={14}
            name={task.technical_details?.plan_hash ? "circle-check" : "circle-alert"}
            width={14}
          />
          {t("runs.approval.planBound")}
        </span>
      </div>
      {summary?.limitations.length ? (
        <ul>
          {summary.limitations.slice(0, 3).map((limitation) => (
            <li key={limitation}>{limitation}</li>
          ))}
        </ul>
      ) : null}
      <div className={styles.approvalFooter}>
        <small>{t("runs.approval.authority")}</small>
        {onOpenAgent ? (
          <Button
            onClick={onOpenAgent}
            size="sm"
            variant={awaitingApproval ? "primary" : "secondary"}
          >
            {awaitingApproval ? t("runs.approval.review") : t("nav.agent")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}

function RunArtifactInspector({
  details,
  loading,
  task,
}: {
  details: ReturnType<typeof useProjectRunDetails>["data"];
  loading: boolean;
  task: TaskLogEntry | null;
}) {
  const { t } = useI18n();
  const artifacts = details?.artifacts.artifacts ?? [];
  const grouped = useMemo(() => {
    const result = new Map<string, typeof artifacts>();
    artifacts.forEach((artifact) => {
      const group = artifact.artifact_type || artifact.kind || t("runs.artifacts.other");
      result.set(group, [...(result.get(group) ?? []), artifact]);
    });
    return [...result.entries()];
  }, [artifacts, t]);
  return (
    <aside aria-label={t("runs.artifactInspector.title")}>
      <div className={styles.sectionHeader}>
        <div>
          <h3>{t("runs.artifactInspector.title")}</h3>
          <p>{t("runs.artifactInspector.description")}</p>
        </div>
        <Badge tone={artifacts.length ? "info" : "neutral"}>{artifacts.length}</Badge>
      </div>
      {loading ? <div className={styles.loadingLine}>{t("common.loading")}</div> : null}
      {!task ? (
        <EmptyState
          title={t("runs.selectRun.title")}
          description={t("runs.artifactInspector.selectRun")}
        />
      ) : grouped.length ? (
        <div className={styles.artifactGroups}>
          {grouped.map(([group, entries]) => (
            <section key={group}>
              <h4>{group}</h4>
              {entries.map((artifact) => (
                <div className={styles.inspectorArtifact} key={artifact.artifact_id}>
                  <span className={styles.artifactIcon}>
                    <Icon height={15} name="results" width={15} />
                  </span>
                  <span>
                    <strong>{artifact.name}</strong>
                    <small>{artifact.relative_path || artifact.path}</small>
                    {artifact.subject_id || artifact.stage_id ? (
                      <small>
                        {[artifact.subject_id, artifact.stage_id].filter(Boolean).join(" · ")}
                      </small>
                    ) : null}
                  </span>
                  <Badge size="sm" tone={artifact.exists ? "success" : "danger"}>
                    {artifact.exists ? t("runs.artifacts.available") : t("common.unavailable")}
                  </Badge>
                </div>
              ))}
            </section>
          ))}
        </div>
      ) : (
        <EmptyState
          title={t("runs.artifacts.emptyTitle")}
          description={t("runs.artifacts.emptyDescription")}
        />
      )}
    </aside>
  );
}

function AgentTaskEvidencePanel({ task }: { task: AgentTaskResponse }) {
  const { t } = useI18n();
  const details = task.technical_details;
  const planOnly = Boolean(
    task.result_summary?.artifacts.some((artifact) => artifact.artifact_type === "reviewed_plan"),
  );
  const ticket = planOnly
    ? t("runs.agentEvidence.ticketNotCreated")
    : (details?.ticket_id ?? t("common.unavailable"));
  const run = planOnly
    ? t("runs.agentEvidence.runNotCreated")
    : (details?.run_id ?? t("common.unavailable"));
  const backend = planOnly
    ? t("runs.agentEvidence.backendPlanOnly")
    : details?.backend
      ? `${details.backend.requested} → ${details.backend.selected ?? t("common.unavailable")}`
      : t("common.unavailable");
  return (
    <Card
      className={styles.agentEvidenceCard}
      role="region"
      aria-label={t("runs.agentEvidence.title")}
    >
      <div className={styles.sectionHeader}>
        <div>
          <h3>{t("runs.agentEvidence.title")}</h3>
          <p>{t("runs.agentEvidence.description")}</p>
        </div>
        <Badge
          tone={
            task.state === "completed"
              ? "success"
              : task.state === "needs_attention"
                ? "danger"
                : "info"
          }
        >
          {t(`agent.state.${task.state}`)}
        </Badge>
      </div>
      <dl className={styles.agentEvidenceGrid}>
        <div>
          <dt>{t("runs.agentEvidence.lifecycle")}</dt>
          <dd>{details?.lifecycle_id ?? task.task_id}</dd>
        </div>
        {planOnly ? (
          <div>
            <dt>{t("runs.agentEvidence.execution")}</dt>
            <dd>{t("runs.agentEvidence.planOnlyExecution")}</dd>
          </div>
        ) : null}
        <div>
          <dt>{t("runs.agentEvidence.ticket")}</dt>
          <dd>{ticket}</dd>
        </div>
        <div>
          <dt>{t("runs.agentEvidence.run")}</dt>
          <dd>{run}</dd>
        </div>
        <div>
          <dt>{t("runs.agentEvidence.backend")}</dt>
          <dd>{backend}</dd>
        </div>
        <div>
          <dt>{t("runs.agentEvidence.plan")}</dt>
          <dd>{details?.plan_hash ?? t("common.unavailable")}</dd>
        </div>
        <div>
          <dt>{t("runs.agentEvidence.evaluation")}</dt>
          <dd>{details?.evaluation_id ?? t("common.unavailable")}</dd>
        </div>
      </dl>
      {details?.backend?.fallback_reason ? (
        <p className={styles.backendFallback}>{details.backend.fallback_reason}</p>
      ) : null}
      <ul className={styles.agentEvidenceLinks}>
        {task.evidence_links.map((link) => (
          <li key={link.id}>
            <span>{localizedEvidenceLabel(link.type, link.label, t)}</span>
            <code>{link.uri}</code>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function localizedEvidenceLabel(
  type: AgentTaskResponse["evidence_links"][number]["type"],
  fallback: string,
  t: Translate,
): string {
  if (type === "reviewed_plan") return t("runs.agentEvidence.reviewedPlan");
  if (type === "execution_ticket") return t("runs.agentEvidence.ticket");
  if (type === "run") return t("runs.agentEvidence.run");
  return fallback;
}

interface RunDetailPanelProps {
  activeTab: RunDetailTab;
  details: ReturnType<typeof useProjectRunDetails>["data"];
  error: string;
  loading: boolean;
  onRetry: () => Promise<unknown>;
  onTabChange: (value: RunDetailTab) => void;
  task: TaskLogEntry;
}

function RunDetailPanel({
  activeTab,
  details,
  error,
  loading,
  onRetry,
  onTabChange,
  task,
}: RunDetailPanelProps) {
  const { t } = useI18n();
  const latestEvents = useMemo(
    () => projectEventsToTaskEvents(task, details?.events, t),
    [details?.events, task, t],
  );
  const diagnostics = useMemo(() => projectRunDiagnostics(task, details), [details, task]);
  const timeline = buildRunTimeline(task, latestEvents, details?.timeline, t);
  const artifactEntries = details?.artifacts.artifacts ?? [];
  const logMessages = useMemo(
    () => projectRunLogMessages(task, details?.logs),
    [details?.logs, task],
  );
  const visibleLogMessages = logMessages.slice(-RUN_LOG_RENDER_LIMIT);
  const nodeInspector = buildNodeInspector(task, diagnostics, latestEvents, details?.timeline, t);
  const [failureActionStatus, setFailureActionStatus] = useState("");
  const [showFailureExplanation, setShowFailureExplanation] = useState(false);
  const hasDiagnostics =
    diagnostics.diagnosis.length ||
    diagnostics.errors.length ||
    diagnostics.warnings.length ||
    diagnostics.external_tool_results.length;

  async function handleCopyDiagnostics() {
    const payload = buildDiagnosticsCopyPayload(task, diagnostics, latestEvents);
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error(t("runs.diagnostics.clipboardApiUnavailable"));
      }
      await navigator.clipboard.writeText(payload);
      setFailureActionStatus(t("runs.diagnostics.copied"));
    } catch {
      setFailureActionStatus(t("runs.diagnostics.clipboardUnavailable"));
    }
  }

  return (
    <section className={styles.detailPanel} aria-label={t("runs.detail.aria")}>
      <div className={styles.detailSummary}>
        <div className={styles.detailTitleBlock}>
          <span className={styles.kicker}>{t("runs.detail.title")}</span>
          <strong>{task.run_name}</strong>
          <small>{task.id}</small>
        </div>
        <div className={styles.detailActions}>
          <Badge tone={statusTone(task.status)}>{statusLabel(task.status, t)}</Badge>
          <Button size="sm" variant="secondary" onClick={() => void onRetry()} disabled={loading}>
            {loading ? t("runs.events.loading") : t("runs.detail.reloadEvents")}
          </Button>
        </div>
      </div>

      <div className={styles.detailFacts} aria-label={t("runs.facts")}>
        <RunFact label={t("runs.table.pipeline")} value={task.pipeline} />
        <RunFact label={t("runs.table.status")} value={statusLabel(task.status, t)} />
        <RunFact label={t("runs.table.progress")} value={`${clampProgress(task.progress)}%`} />
        <RunFact
          label={t("runs.table.started")}
          value={details?.detail.summary_preview?.started_at ?? task.started_at}
        />
        <RunFact
          label={t("runs.table.duration")}
          value={projectRunDuration(details?.detail, task, t)}
        />
        <RunFact label={t("runs.table.triggeredBy")} value={task.owner} />
        <RunFact
          label={t("runs.fact.execution")}
          value={task.execution_mode || t("runs.notReported")}
        />
        <RunFact label={t("runs.fact.result")} value={formatResultFact(task, t)} />
      </div>

      <div className={styles.timelinePanel}>
        <div className={styles.panelHeader}>
          <span>{t("runs.timeline")}</span>
          <small>{t("runs.timeline.checkpoints", { count: timeline.length })}</small>
        </div>
        <ol className={styles.timeline} aria-label={t("runs.timeline")}>
          {timeline.map((item, index) => (
            <li key={`${item.label}-${item.message}-${index}`} data-status={item.status}>
              <span>{item.label}</span>
              <p>{item.message}</p>
              <small>{item.time}</small>
            </li>
          ))}
        </ol>
      </div>

      <div className={styles.nodeInspector} aria-label={t("runs.node.aria")}>
        <div className={styles.panelHeader}>
          <span>{t("runs.node.title")}</span>
          <small>{nodeInspector.source}</small>
        </div>
        <div className={styles.nodeGrid}>
          <RunFact label={t("runs.node.node")} value={nodeInspector.node} />
          <RunFact label={t("runs.node.state")} value={nodeInspector.state} />
          <RunFact label={t("runs.node.evidence")} value={nodeInspector.evidence} />
          <RunFact label={t("runs.node.retry")} value={nodeInspector.retry} />
        </div>
      </div>

      <SegmentedControl
        aria-label={t("runs.sections")}
        value={activeTab}
        onChange={(value) => onTabChange(value as RunDetailTab)}
        options={[
          { label: t("runs.tab.events"), value: "events" },
          { label: t("runs.tab.logs"), value: "logs" },
          { label: t("runs.tab.diagnostics"), value: "diagnostics" },
          { label: t("runs.tab.artifacts"), value: "artifacts" },
          { label: t("runs.tab.audit"), value: "audit" },
        ]}
      />

      <div className={styles.tabPanel}>
        {activeTab === "events" ? (
          <section aria-label={t("runs.events.aria")}>
            {loading ? <div className={styles.loadingLine}>{t("runs.events.loading")}</div> : null}
            {error ? <div className={styles.errorLine}>{error}</div> : null}
            <div className={styles.eventList}>
              {latestEvents.map((event) => (
                <div
                  className={styles.eventRow}
                  key={`${event.id}-${event.timestamp}-${event.message}`}
                >
                  <span>{event.timestamp}</span>
                  <strong>{clampProgress(event.progress)}%</strong>
                  <p>{event.message}</p>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {activeTab === "logs" ? (
          <section aria-label={t("runs.logs.aria")}>
            {logMessages.length ? (
              <>
                {logMessages.length > visibleLogMessages.length ? (
                  <div className={styles.trimNote} role="status">
                    {t("runs.logs.trimmed", {
                      visible: visibleLogMessages.length,
                      total: logMessages.length,
                    })}
                  </div>
                ) : null}
                <div className={styles.logList}>
                  {visibleLogMessages.map((message, index) => (
                    <p key={`${message}-${index}`}>{message}</p>
                  ))}
                </div>
              </>
            ) : (
              <EmptyState
                title={t("runs.logs.emptyTitle")}
                description={t("runs.logs.emptyDescription")}
              />
            )}
          </section>
        ) : null}

        {activeTab === "diagnostics" ? (
          <section aria-label={t("runs.diagnostics.aria")}>
            {task.status === "failed" ? (
              <div className={styles.failureBanner} role="alert">
                {t("runs.diagnostics.failedBanner")}
              </div>
            ) : null}
            {task.status === "failed" ? (
              <div className={styles.failureActions} aria-label={t("runs.diagnostics.actions")}>
                <div>
                  <strong>{t("runs.diagnostics.failedResponse")}</strong>
                  <p>{t("runs.diagnostics.actionsDescription")}</p>
                </div>
                <div className={styles.failureButtonRow}>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setShowFailureExplanation((value) => !value)}
                  >
                    {t("runs.diagnostics.explain")}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={handleCopyDiagnostics}>
                    {t("runs.diagnostics.copy")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled
                    title={t("runs.diagnostics.retryDisabled")}
                  >
                    {t("runs.diagnostics.retryAllowed")}
                  </Button>
                </div>
                {showFailureExplanation ? (
                  <div
                    className={styles.failureExplanation}
                    aria-label={t("runs.diagnostics.explanation")}
                  >
                    <span>{nodeInspector.node}</span>
                    <p>{nodeInspector.evidence}</p>
                  </div>
                ) : null}
                <small>{failureActionStatus || t("runs.diagnostics.retryDisabled")}</small>
              </div>
            ) : null}
            {hasDiagnostics ? (
              <div className={styles.diagnosticsList}>
                {diagnostics.errors.map((message, index) => (
                  <DiagnosticItem
                    key={`error-${index}`}
                    tone="danger"
                    label={t("runs.diagnostics.error")}
                    message={message}
                  />
                ))}
                {diagnostics.warnings.map((message, index) => (
                  <DiagnosticItem
                    key={`warning-${index}`}
                    tone="warning"
                    label={t("runs.diagnostics.warning")}
                    message={message}
                  />
                ))}
                {diagnostics.diagnosis.slice(0, DIAGNOSIS_RENDER_LIMIT).map((item, index) => (
                  <DiagnosticItem
                    key={`diagnosis-${index}`}
                    tone={diagnosticTone(item.severity)}
                    label={String(item.code || item.severity || t("runs.diagnostics.defaultLabel"))}
                    message={String(item.message || t("runs.diagnostics.defaultMessage"))}
                  />
                ))}
                {diagnostics.external_tool_results
                  .slice(0, EXTERNAL_TOOL_RENDER_LIMIT)
                  .map((result, index) => (
                    <DiagnosticItem
                      key={`tool-${index}`}
                      tone={String(result.returncode ?? "0") === "0" ? "info" : "warning"}
                      label={String(
                        result.command ||
                          result.function ||
                          t("runs.diagnostics.externalTool", { index: index + 1 }),
                      )}
                      message={t("runs.diagnostics.returnCode", {
                        code: String(result.returncode ?? "n/a"),
                      })}
                    />
                  ))}
              </div>
            ) : (
              <EmptyState
                title={t("runs.diagnostics.emptyTitle")}
                description={t("runs.diagnostics.emptyDescription")}
              />
            )}
          </section>
        ) : null}

        {activeTab === "artifacts" ? (
          <section aria-label={t("runs.artifacts.aria")}>
            {task.result_path ? (
              <div className={styles.artifactPath}>
                <span>{t("runs.artifacts.resultPath")}</span>
                <strong>{task.result_path}</strong>
              </div>
            ) : null}
            {artifactEntries.length ? (
              <div className={styles.artifactList}>
                {artifactEntries.map((artifact) => (
                  <div className={styles.artifactRow} key={artifact.artifact_id}>
                    <span>{artifact.name}</span>
                    <strong>{artifact.relative_path || artifact.path}</strong>
                  </div>
                ))}
              </div>
            ) : !task.result_path ? (
              <EmptyState
                title={t("runs.artifacts.emptyTitle")}
                description={t("runs.artifacts.emptyDescription")}
              />
            ) : null}
          </section>
        ) : null}

        {activeTab === "audit" ? (
          <section className={styles.auditPanel} aria-label={t("runs.audit.aria")}>
            {details?.detail.run_link.audit_id ? (
              <div className={styles.auditRecord}>
                <span>{t("runs.audit.package")}</span>
                <strong>{details.detail.run_link.audit_id}</strong>
              </div>
            ) : (
              <EmptyState
                title={t("runs.audit.emptyTitle")}
                description={t("runs.audit.emptyDescription")}
              />
            )}
            {details?.detail.run_link.dispatch_id ? (
              <div className={styles.auditRecord}>
                <span>{t("runs.audit.dispatch")}</span>
                <strong>{details.detail.run_link.dispatch_id}</strong>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>
    </section>
  );
}

function RunsOverview({ tasks }: { tasks: TaskLogEntry[] }) {
  const { t } = useI18n();
  const running = tasks.filter((task) => task.status === "running").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const pending = tasks.filter((task) => task.status === "pending").length;

  return (
    <section className={styles.summaryGrid} aria-label={t("runs.overview.aria")}>
      <Card tone="muted">
        <div className={styles.summaryItem}>
          <span>{t("runs.overview.total")}</span>
          <strong>{tasks.length}</strong>
          <small>{t("runs.overview.loaded")}</small>
        </div>
      </Card>
      <Card>
        <div className={styles.summaryItem}>
          <span>{t("runs.overview.active")}</span>
          <strong>{running + pending}</strong>
          <small>{t("runs.overview.activeDetail", { running, pending })}</small>
        </div>
      </Card>
      <Card>
        <div className={styles.summaryItem}>
          <span>{t("runs.overview.failed")}</span>
          <strong>{failed}</strong>
          <small>{t("runs.overview.failedDetail")}</small>
        </div>
      </Card>
      <Card>
        <div className={styles.summaryItem}>
          <span>{t("runs.overview.completed")}</span>
          <strong>{completed}</strong>
          <small>{t("runs.overview.completedDetail")}</small>
        </div>
      </Card>
    </section>
  );
}

function runListEmptyMessage({
  error,
  filtered,
  loading,
  projectId,
  taskCount,
  t,
}: {
  error: string;
  filtered: boolean;
  loading: boolean;
  projectId: string | null;
  taskCount: number;
  t: Translate;
}): string {
  if (!projectId) return t("runs.empty.selectProject");
  if (loading && taskCount === 0) return t("runs.empty.loading");
  if (error && taskCount === 0) return t("runs.empty.unavailable");
  if (filtered) {
    return t("runs.empty.filtered");
  }
  return t("runs.empty.none");
}

function RunProgress({ value }: { value: number }) {
  const { t } = useI18n();
  const progress = clampProgress(value);

  return (
    <div className={styles.progressCell} aria-label={t("runs.progressAria", { progress })}>
      <span className={styles.progressTrack}>
        <span className={styles.progressFill} style={{ width: `${progress}%` }} />
      </span>
      <strong>{progress}%</strong>
    </div>
  );
}

function RunFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function formatResultFact(task: TaskLogEntry, t: Translate): string {
  if (task.result_path) return task.result_path;
  if (task.status === "running" || task.status === "pending") return t("runs.result.pending");
  if (task.status === "failed") return t("runs.result.failed");
  return t("runs.result.none");
}

function DiagnosticItem({
  label,
  message,
  tone,
}: {
  label: string;
  message: string;
  tone: "danger" | "info" | "neutral" | "warning";
}) {
  return (
    <div className={styles.diagnosticItem} data-tone={tone}>
      <span>{label}</span>
      <p>{message}</p>
    </div>
  );
}

function eventsFromLogs(task: TaskLogEntry, t: Translate): TaskEvent[] {
  const logs = task.logs.length ? task.logs : [t("runs.events.none")];
  return logs.map((message, index) => ({
    id: index,
    task_id: task.id,
    status: task.status,
    progress: task.progress,
    message,
    timestamp: task.started_at,
    result_path: task.result_path,
    source: "task-log",
    metadata: {},
  }));
}

function projectEventsToTaskEvents(
  task: TaskLogEntry,
  response: ProjectRunEventsResponse | undefined,
  t: Translate,
): TaskEvent[] {
  if (!response?.events.length) {
    return eventsFromLogs(task, t);
  }
  return response.events
    .map((event, index) => ({
      id: index,
      task_id: task.id,
      status: task.status,
      progress:
        typeof event.metadata?.progress === "number" ? event.metadata.progress : task.progress,
      message: localizeRunEventMessage(event.message, t),
      timestamp: event.timestamp ?? task.started_at,
      result_path: task.result_path,
      source: event.source,
      metadata: {
        ...event.metadata,
        level: event.level,
        node_id: event.node_id,
        path: event.path,
        subject_id: event.subject_id,
      },
    }))
    .sort((left, right) => compareEventTimestamps(left.timestamp, right.timestamp));
}

function projectRunLogMessages(
  task: TaskLogEntry,
  response: ProjectRunLogsResponse | undefined,
): string[] {
  if (!response) return task.logs;
  const content = response.logs.flatMap((log) => {
    if (log.content) {
      return log.content
        .split(/\r?\n/)
        .map((line) => line.trimEnd())
        .filter(Boolean);
    }
    return log.exists ? [log.relative_path || log.path] : [];
  });
  return [...response.warnings, ...response.errors, ...content];
}

function projectRunDiagnostics(
  task: TaskLogEntry,
  details: ProjectRunDetails | null,
): TaskDiagnostics {
  if (!details) {
    return {
      ok: false,
      task_id: task.id,
      status: task.status,
      diagnosis: [],
      external_tool_results: [],
      logs: [],
      artifacts: {},
      approval: null,
      errors: [],
      warnings: [],
    };
  }
  const nodeErrors = details.timeline.nodes.flatMap((node) => node.errors);
  const nodeWarnings = details.timeline.nodes.flatMap((node) => node.warnings);
  return {
    ok: details.events.ok && details.logs.ok && details.artifacts.ok && details.timeline.ok,
    task_id: task.id,
    status: task.status,
    diagnosis: details.timeline.nodes.map((node) => ({
      code: node.node_id,
      message: node.errors[0] || node.warnings[0] || node.state,
      node_id: node.node_id,
      retry_eligible: node.retry_eligible,
      severity: node.errors.length ? "error" : node.warnings.length ? "warning" : "info",
      state: node.state,
    })),
    external_tool_results: [],
    logs: [],
    artifacts: Object.fromEntries(
      details.artifacts.artifacts.map((artifact) => [
        artifact.name,
        artifact.relative_path || artifact.path,
      ]),
    ),
    approval: null,
    errors: [
      ...details.events.errors,
      ...details.logs.errors,
      ...details.timeline.errors,
      ...nodeErrors,
    ],
    warnings: [
      ...(details.detail.warnings ?? []),
      ...details.events.warnings,
      ...details.logs.warnings,
      ...details.artifacts.warnings,
      ...details.timeline.warnings,
      ...nodeWarnings,
    ],
  };
}

function buildRunTimeline(
  task: TaskLogEntry,
  events: TaskEvent[],
  stateTimeline: ProjectRunStateTimelineResponse | undefined,
  t: Translate,
) {
  if (stateTimeline?.events.length) {
    const recentEvents = [...stateTimeline.events]
      .sort((left, right) => {
        if (!left.timestamp) return right.timestamp ? 1 : 0;
        if (!right.timestamp) return -1;
        return compareEventTimestamps(left.timestamp, right.timestamp);
      })
      .slice(-5);
    return recentEvents.map((event, index) => ({
      label:
        event.node_id ||
        (index === recentEvents.length - 1
          ? t("runs.timeline.latest")
          : t("runs.timeline.step", { index: index + 1 })),
      message: localizeRunEventMessage(event.message || event.state, t),
      status: task.status,
      time: event.timestamp ?? task.started_at,
    }));
  }
  const checkpoints = events.slice(-5).map((event, index) => ({
    label:
      index === events.slice(-5).length - 1
        ? t("runs.timeline.latest")
        : t("runs.timeline.step", { index: index + 1 }),
    message: event.message,
    status: event.status,
    time: event.timestamp,
  }));

  if (checkpoints.length) {
    return checkpoints;
  }

  return [
    {
      label: t("runs.timeline.latest"),
      message: task.logs[task.logs.length - 1] ?? t("runs.events.none"),
      status: task.status,
      time: task.started_at,
    },
  ];
}

function compareEventTimestamps(left: string, right: string): number {
  const leftMillis = Date.parse(left);
  const rightMillis = Date.parse(right);
  if (Number.isFinite(leftMillis) && Number.isFinite(rightMillis)) {
    return leftMillis - rightMillis;
  }
  if (Number.isFinite(leftMillis)) return -1;
  if (Number.isFinite(rightMillis)) return 1;
  return left.localeCompare(right);
}

function localizeRunEventMessage(message: string, t: Translate): string {
  const exactKeys: Record<string, Parameters<Translate>[0]> = {
    "Pipeline execution started.": "runs.event.pipelineStarted",
    "Pipeline execution finished.": "runs.event.pipelineFinished",
    "Run link created.": "runs.event.runLinkCreated",
  };
  const exactKey = exactKeys[message];
  if (exactKey) return t(exactKey);

  const runLinkCreated = message.match(/^Run link created with status ([^.]+)\.$/);
  if (runLinkCreated) {
    return t("runs.event.runLinkCreatedWithStatus", {
      status: localizeRunEventStatus(runLinkCreated[1], t),
    });
  }
  const runLinkUpdated = message.match(/^Run link updated to status ([^.]+)\.$/);
  if (runLinkUpdated) {
    return t("runs.event.runLinkUpdatedWithStatus", {
      status: localizeRunEventStatus(runLinkUpdated[1], t),
    });
  }
  const pipelineFinished = message.match(/^Pipeline finished(?::| with status) ([^.]+)\.$/);
  if (pipelineFinished) {
    return t("runs.event.pipelineFinishedWithStatus", {
      status: localizeRunEventStatus(pipelineFinished[1], t),
    });
  }
  return message;
}

function localizeRunEventStatus(status: string, t: Translate): string {
  const normalized = status.trim().toLowerCase();
  if (["success", "succeeded", "completed"].includes(normalized)) {
    return t("runs.event.status.success");
  }
  if (["failed", "failure", "error"].includes(normalized)) {
    return t("runs.event.status.failed");
  }
  if (normalized === "running") return t("runs.event.status.running");
  if (["partial", "needs_attention"].includes(normalized)) {
    return t("runs.event.status.partial");
  }
  return status;
}

function buildNodeInspector(
  task: TaskLogEntry,
  diagnostics: TaskDiagnostics,
  events: TaskEvent[],
  stateTimeline: ProjectRunStateTimelineResponse | undefined,
  t: Translate,
) {
  const diagnostic = pickPrimaryDiagnostic(diagnostics);
  const node =
    firstStringValue(diagnostic, ["node_id", "node", "node_name", "stage", "step_id", "code"]) ||
    task.pipeline;
  const evidence =
    diagnostics.errors[0] ||
    firstStringValue(diagnostic, ["message", "error", "detail", "recommendation"]) ||
    events[events.length - 1]?.message ||
    task.logs[task.logs.length - 1] ||
    t("runs.node.noEvidence");
  const retryEligible =
    Boolean(stateTimeline?.retry_eligible) ||
    Boolean(stateTimeline?.nodes.some((node) => node.retry_eligible));
  const retry = retryEligible ? t("runs.diagnostics.retryDisabled") : t("runs.node.retryUnknown");

  return {
    evidence,
    node,
    retry,
    source: diagnostic ? t("runs.node.diagnosticSource") : t("runs.node.eventSource"),
    state: statusLabel(task.status, t),
  };
}

function pickPrimaryDiagnostic(diagnostics: TaskDiagnostics): Record<string, unknown> | null {
  const failed = diagnostics.diagnosis.find((item) => {
    const severity = String(item.severity ?? item.status ?? "").toLowerCase();
    return severity.includes("error") || severity.includes("fail");
  });
  return failed ?? diagnostics.diagnosis[0] ?? null;
}

function firstStringValue(record: Record<string, unknown> | null, keys: string[]): string {
  if (!record) return "";
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

function buildDiagnosticsCopyPayload(
  task: TaskLogEntry,
  diagnostics: TaskDiagnostics,
  events: TaskEvent[],
): string {
  return JSON.stringify(
    {
      task_id: task.id,
      run_name: task.run_name,
      pipeline: task.pipeline,
      status: task.status,
      errors: diagnostics.errors,
      warnings: diagnostics.warnings,
      diagnosis: diagnostics.diagnosis,
      latest_events: events.slice(-5).map((event) => ({
        message: event.message,
        progress: event.progress,
        status: event.status,
        timestamp: event.timestamp,
      })),
    },
    null,
    2,
  );
}

function projectRunDuration(
  detail: ProjectRunDetailResponse | undefined,
  task: TaskLogEntry,
  t: Translate,
): string {
  if (task.duration) return task.duration;
  const startedAt = detail?.summary_preview?.started_at;
  const finishedAt = detail?.summary_preview?.finished_at;
  if (!startedAt || !finishedAt) return t("runs.inProgress");
  const durationMs = Date.parse(finishedAt) - Date.parse(startedAt);
  if (!Number.isFinite(durationMs) || durationMs < 0) return t("runs.notReported");
  const totalSeconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return minutes ? `${minutes}m ${seconds}s` : `${seconds}s`;
}

function diagnosticTone(severity: unknown): "danger" | "info" | "neutral" | "warning" {
  const value = String(severity ?? "").toLowerCase();
  if (value.includes("error") || value.includes("fail")) return "danger";
  if (value.includes("warn")) return "warning";
  if (value.includes("info")) return "info";
  return "neutral";
}

function statusLabel(status: TaskStatus, t: Translate): string {
  if (status === "completed") return t("runs.status.completed");
  if (status === "partial") return t("runs.status.partial");
  if (status === "failed") return t("runs.status.failed");
  if (status === "running") return t("runs.status.running");
  if (status === "pending") return t("runs.status.pending");
  return t("runs.status.disconnected");
}

function clampProgress(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value)));
}

function statusTone(status: TaskStatus): "neutral" | "info" | "success" | "warning" | "danger" {
  if (status === "completed") return "success";
  if (status === "partial") return "warning";
  if (status === "failed") return "danger";
  if (status === "running") return "info";
  if (status === "pending" || status === "disconnected") return "warning";
  return "neutral";
}
